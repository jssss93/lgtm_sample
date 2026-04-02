import json
import logging
import os
import threading
import time

import httpx
from fastapi import FastAPI, HTTPException
from openai import AsyncAzureOpenAI
from pydantic import BaseModel

from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

# ──────────────────────────── Config ────────────────────────────
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "ai-agent")
AGENT_TYPE = os.getenv("AGENT_TYPE", "default")

# ──────────────────────────── OTel: Resource ────────────────────
resource = Resource(attributes={
    "service.name": SERVICE_NAME,
    "agent.type": AGENT_TYPE,
})

# ──────────────────────────── OTel: Traces ──────────────────────
trace_provider = TracerProvider(resource=resource)
trace_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
)
trace.set_tracer_provider(trace_provider)
tracer = trace.get_tracer(__name__)

# ──────────────────────────── OTel: Metrics ─────────────────────
metric_reader = PeriodicExportingMetricReader(
    OTLPMetricExporter(endpoint=OTEL_ENDPOINT, insecure=True),
    export_interval_millis=5000,
)
meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
metrics.set_meter_provider(meter_provider)
meter = metrics.get_meter(__name__)

agent_run_counter = meter.create_counter("agent.run.count", description="Number of agent runs")
agent_error_counter = meter.create_counter("agent.error.count", description="Number of agent errors")
llm_call_duration = meter.create_histogram("llm.call.duration", description="LLM call duration", unit="s")
token_usage_counter = meter.create_counter("llm.token.usage", description="Token usage")
cost_counter = meter.create_counter("llm.cost.usd", description="LLM cost in USD")
request_token_histogram = meter.create_histogram("llm.tokens.per_request", description="Tokens per request")

# ──────────────────────────── OTel: Logs ────────────────────────
logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(
    BatchLogRecordProcessor(OTLPLogExporter(endpoint=OTEL_ENDPOINT, insecure=True))
)
handler = LoggingHandler(logger_provider=logger_provider)
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

# ──────────────────────────── OTel: Instrumentors ───────────────
HTTPXClientInstrumentor().instrument()

# ──────────────────────────── AOAI Client ───────────────────────
aoai = AsyncAzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
    api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
)

# ──────────────────────────── Agent Profiles ────────────────────
AGENT_PROFILES = {
    "orchestrator": {
        "deployment": "gpt-4.1",
        "system_prompt": (
            "You are an orchestrator agent. Analyze the user's query and decide which "
            "specialist agents to call.\n"
            "- call_search: for factual/knowledge questions\n"
            "- call_summarizer: for text summarization requests\n"
            "- call_coder: for code generation or review tasks\n"
            "You may call multiple agents if the query needs it. "
            "After receiving agent results, synthesize a final answer."
        ),
    },
    "search": {
        "deployment": "gpt-4.1-mini",
        "system_prompt": (
            "You are a search agent. Answer factual questions accurately and concisely. "
            "Provide structured, informative answers. Keep responses under 200 words."
        ),
    },
    "summarizer": {
        "deployment": "gpt-4.1-mini",
        "system_prompt": (
            "You are a summarization agent. Given text, produce a clear and concise summary. "
            "Preserve key facts and main ideas. Keep summaries under 150 words."
        ),
    },
    "coder": {
        "deployment": "gpt-4.1",
        "system_prompt": (
            "You are a code agent. Generate clean, well-commented code. "
            "When reviewing code, identify bugs and suggest improvements. "
            "Always include brief explanations with your code."
        ),
    },
}

profile = AGENT_PROFILES.get(AGENT_TYPE, AGENT_PROFILES["search"])

# ──────────────────────────── Cost Tracking ─────────────────────
# Azure OpenAI pricing (per 1M tokens, USD)
PRICING = {
    "gpt-4.1":      {"prompt": 2.00, "completion": 8.00},
    "gpt-4.1-mini": {"prompt": 0.40, "completion": 1.60},
}

_stats_lock = threading.Lock()
_stats = {
    "total_requests": 0,
    "total_prompt_tokens": 0,
    "total_completion_tokens": 0,
    "total_cost_usd": 0.0,
    "by_model": {},
    "started_at": time.time(),
}

# ──────────────────────────── Orchestrator Tools ────────────────
ORCHESTRATOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "call_search",
            "description": "Call the search agent for factual or knowledge questions",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_summarizer",
            "description": "Call the summarizer agent to summarize text",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to summarize"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_coder",
            "description": "Call the coder agent for code generation or review",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The coding task"},
                },
                "required": ["query"],
            },
        },
    },
]

SUB_AGENT_URLS = {
    "call_search": os.getenv("SEARCH_AGENT_URL", "http://agent-search:8000"),
    "call_summarizer": os.getenv("SUMMARIZER_AGENT_URL", "http://agent-summarizer:8000"),
    "call_coder": os.getenv("CODER_AGENT_URL", "http://agent-coder:8000"),
}


# ──────────────────────────── Models ────────────────────────────
class AgentRequest(BaseModel):
    query: str
    context: str | None = None
    params: dict[str, str | int | float | bool] | None = None  # custom params → span attributes


class AgentResponse(BaseModel):
    agent_type: str
    model: str
    result: str
    tokens: dict | None = None
    cost_usd: float | None = None


# ──────────────────────────── Helpers ───────────────────────────
def _calc_cost(deployment: str, prompt_tokens: int, completion_tokens: int) -> float:
    p = PRICING.get(deployment, {"prompt": 0, "completion": 0})
    return round(prompt_tokens * p["prompt"] / 1_000_000 + completion_tokens * p["completion"] / 1_000_000, 6)


async def call_aoai(deployment: str, messages: list[dict], tools: list | None = None) -> dict:
    with tracer.start_as_current_span("llm-call") as span:
        span.set_attribute("llm.model", deployment)
        span.set_attribute("llm.message_count", len(messages))

        start = time.time()
        kwargs = {"model": deployment, "messages": messages, "temperature": 0.7}
        if tools:
            kwargs["tools"] = tools
        response = await aoai.chat.completions.create(**kwargs)
        duration = time.time() - start

        usage = response.usage
        span.set_attribute("llm.prompt_tokens", usage.prompt_tokens)
        span.set_attribute("llm.completion_tokens", usage.completion_tokens)
        span.set_attribute("llm.total_tokens", usage.total_tokens)
        span.set_attribute("llm.duration", round(duration, 3))
        span.set_attribute("llm.cost_usd", round(
            usage.prompt_tokens * PRICING.get(deployment, {}).get("prompt", 0) / 1_000_000
            + usage.completion_tokens * PRICING.get(deployment, {}).get("completion", 0) / 1_000_000, 6
        ))

        llm_call_duration.record(duration, {"llm.model": deployment, "agent.type": AGENT_TYPE})
        token_usage_counter.add(usage.prompt_tokens, {"llm.model": deployment, "type": "prompt"})
        token_usage_counter.add(usage.completion_tokens, {"llm.model": deployment, "type": "completion"})
        request_token_histogram.record(usage.total_tokens, {"llm.model": deployment, "agent.type": AGENT_TYPE})

        # Cost tracking
        model_pricing = PRICING.get(deployment, {"prompt": 0, "completion": 0})
        call_cost = (
            usage.prompt_tokens * model_pricing["prompt"] / 1_000_000
            + usage.completion_tokens * model_pricing["completion"] / 1_000_000
        )
        cost_counter.add(call_cost, {"llm.model": deployment, "agent.type": AGENT_TYPE})
        with _stats_lock:
            _stats["total_requests"] += 1
            _stats["total_prompt_tokens"] += usage.prompt_tokens
            _stats["total_completion_tokens"] += usage.completion_tokens
            _stats["total_cost_usd"] += call_cost
            if deployment not in _stats["by_model"]:
                _stats["by_model"][deployment] = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "calls": 0}
            m = _stats["by_model"][deployment]
            m["prompt_tokens"] += usage.prompt_tokens
            m["completion_tokens"] += usage.completion_tokens
            m["cost_usd"] += call_cost
            m["calls"] += 1

        logger.info("LLM call completed", extra={
            "model": deployment,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "duration": round(duration, 3),
            "cost_usd": round(call_cost, 6),
        })
        return response


async def execute_tool_call(tool_call, params: dict | None = None) -> str:
    fn_name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)
    url = SUB_AGENT_URLS.get(fn_name)

    if not url:
        return f"Error: unknown tool '{fn_name}'"

    with tracer.start_as_current_span(f"sub-agent-call") as span:
        span.set_attribute("sub_agent.name", fn_name)
        span.set_attribute("sub_agent.url", url)

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                query = args.get("query") or args.get("text", "")
                body = {"query": query}
                if params:
                    body["params"] = params
                resp = await client.post(f"{url}/run", json=body)
                resp.raise_for_status()
                result = resp.json()["result"]
                span.set_attribute("sub_agent.status", "success")
                logger.info(f"Sub-agent call completed", extra={"tool": fn_name, "status": "success"})
                return result
        except Exception as e:
            span.set_attribute("sub_agent.status", "error")
            span.set_attribute("error", True)
            logger.error(f"Sub-agent call failed", extra={"tool": fn_name, "error": str(e)})
            return f"Error calling {fn_name}: {e}"


# ──────────────────────────── FastAPI ───────────────────────────
app = FastAPI(title=f"AI Agent - {AGENT_TYPE}")
FastAPIInstrumentor.instrument_app(app)


@app.post("/run")
async def run_agent(req: AgentRequest):
    with tracer.start_as_current_span("agent-run") as span:
        span.set_attribute("agent.type", AGENT_TYPE)
        span.set_attribute("request.query", req.query[:200])
        # custom params → span attributes
        if req.params:
            for k, v in req.params.items():
                span.set_attribute(f"param.{k}", v)
        agent_run_counter.add(1, {"agent.type": AGENT_TYPE})
        logger.info("Agent run started", extra={"agent_type": AGENT_TYPE, "query": req.query[:100], "params": req.params})

        try:
            if AGENT_TYPE == "orchestrator":
                return await _run_orchestrator(req, span)
            else:
                return await _run_sub_agent(req)
        except Exception as e:
            span.set_attribute("error", True)
            agent_error_counter.add(1, {"agent.type": AGENT_TYPE})
            logger.error("Agent run failed", extra={"agent_type": AGENT_TYPE, "error": str(e)})
            raise HTTPException(status_code=500, detail=str(e))


async def _run_orchestrator(req: AgentRequest, span) -> AgentResponse:
    messages = [
        {"role": "system", "content": profile["system_prompt"]},
        {"role": "user", "content": req.query},
    ]

    # Step 1: routing decision
    response = await call_aoai(profile["deployment"], messages, tools=ORCHESTRATOR_TOOLS)
    choice = response.choices[0]

    # No tool calls → direct answer
    if not choice.message.tool_calls:
        p, c = response.usage.prompt_tokens, response.usage.completion_tokens
        cost = _calc_cost(profile["deployment"], p, c)
        return AgentResponse(
            agent_type="orchestrator",
            model=profile["deployment"],
            result=choice.message.content or "",
            tokens={"prompt": p, "completion": c},
            cost_usd=cost,
        )

    # Step 2: execute tool calls
    messages.append(choice.message.model_dump())
    agents_called = []
    for tc in choice.message.tool_calls:
        agents_called.append(tc.function.name)
        result = await execute_tool_call(tc, params=req.params)
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": result,
        })

    span.set_attribute("orchestrator.agents_called", ",".join(agents_called))

    # Step 3: final aggregation
    final = await call_aoai(profile["deployment"], messages)
    total_p = response.usage.prompt_tokens + final.usage.prompt_tokens
    total_c = response.usage.completion_tokens + final.usage.completion_tokens
    cost = _calc_cost(profile["deployment"], total_p, total_c)
    return AgentResponse(
        agent_type="orchestrator",
        model=profile["deployment"],
        result=final.choices[0].message.content or "",
        tokens={"prompt": total_p, "completion": total_c},
        cost_usd=cost,
    )


async def _run_sub_agent(req: AgentRequest) -> AgentResponse:
    messages = [
        {"role": "system", "content": profile["system_prompt"]},
        {"role": "user", "content": req.query},
    ]
    if req.context:
        messages.insert(1, {"role": "user", "content": f"Context:\n{req.context}"})

    response = await call_aoai(profile["deployment"], messages)
    p, c = response.usage.prompt_tokens, response.usage.completion_tokens
    cost = _calc_cost(profile["deployment"], p, c)
    return AgentResponse(
        agent_type=AGENT_TYPE,
        model=profile["deployment"],
        result=response.choices[0].message.content or "",
        tokens={"prompt": p, "completion": c},
        cost_usd=cost,
    )


@app.get("/health")
def health():
    return {"status": "ok", "agent_type": AGENT_TYPE, "service": SERVICE_NAME}


@app.get("/stats")
def stats():
    with _stats_lock:
        uptime = time.time() - _stats["started_at"]
        return {
            "agent_type": AGENT_TYPE,
            "service": SERVICE_NAME,
            "uptime_seconds": round(uptime, 1),
            "total_requests": _stats["total_requests"],
            "total_tokens": {
                "prompt": _stats["total_prompt_tokens"],
                "completion": _stats["total_completion_tokens"],
                "total": _stats["total_prompt_tokens"] + _stats["total_completion_tokens"],
            },
            "total_cost_usd": round(_stats["total_cost_usd"], 6),
            "by_model": {
                model: {
                    **data,
                    "cost_usd": round(data["cost_usd"], 6),
                    "avg_tokens_per_call": round(
                        (data["prompt_tokens"] + data["completion_tokens"]) / max(data["calls"], 1), 1
                    ),
                }
                for model, data in _stats["by_model"].items()
            },
            "pricing_per_1m_tokens": PRICING,
        }
