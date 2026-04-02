import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict

import httpx
from fastapi import FastAPI, HTTPException
from openai import AsyncAzureOpenAI, RateLimitError, APIStatusError
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
MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "300"))
CACHE_MAX_SIZE = int(os.getenv("CACHE_MAX_SIZE", "100"))
PROMPT_LOG_MAX_LEN = int(os.getenv("PROMPT_LOG_MAX_LEN", "500"))

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
# [NEW] Rate limit & retry metrics
rate_limit_counter = meter.create_counter("llm.rate_limit.count", description="429 rate limit hits")
retry_counter = meter.create_counter("llm.retry.count", description="LLM call retries")
# [NEW] Cache metrics
cache_hit_counter = meter.create_counter("cache.hit.count", description="Cache hits")
cache_miss_counter = meter.create_counter("cache.miss.count", description="Cache misses")

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
    "total_retries": 0,
    "total_rate_limits": 0,
    "total_cache_hits": 0,
    "total_cache_misses": 0,
    "by_model": {},
    "by_user": {},
    "started_at": time.time(),
}

# ──────────────────────────── Semantic Cache ────────────────────
_cache_lock = threading.Lock()
_cache: OrderedDict[str, tuple[str, float, dict]] = OrderedDict()  # hash → (result, timestamp, meta)


def _cache_key(deployment: str, query: str) -> str:
    normalized = query.strip().lower()
    return hashlib.sha256(f"{deployment}:{normalized}".encode()).hexdigest()


def cache_get(deployment: str, query: str) -> tuple[str, dict] | None:
    key = _cache_key(deployment, query)
    with _cache_lock:
        if key in _cache:
            result, ts, meta = _cache[key]
            if time.time() - ts < CACHE_TTL:
                _cache.move_to_end(key)
                return result, meta
            else:
                del _cache[key]
    return None


def cache_set(deployment: str, query: str, result: str, meta: dict):
    key = _cache_key(deployment, query)
    with _cache_lock:
        _cache[key] = (result, time.time(), meta)
        if len(_cache) > CACHE_MAX_SIZE:
            _cache.popitem(last=False)


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
    params: dict[str, str | int | float | bool] | None = None
    model_override: str | None = None  # [NEW] A/B test: override deployment model


class AgentResponse(BaseModel):
    agent_type: str
    model: str
    result: str
    tokens: dict | None = None
    cost_usd: float | None = None
    cached: bool = False
    retries: int = 0


# ──────────────────────────── Helpers ───────────────────────────
def _calc_cost(deployment: str, prompt_tokens: int, completion_tokens: int) -> float:
    p = PRICING.get(deployment, {"prompt": 0, "completion": 0})
    return round(prompt_tokens * p["prompt"] / 1_000_000 + completion_tokens * p["completion"] / 1_000_000, 6)


def _track_user_cost(params: dict | None, cost: float, tokens: int):
    if not params:
        return
    user_id = str(params.get("user_id", ""))
    session_id = str(params.get("session_id", ""))
    if not user_id and not session_id:
        return
    with _stats_lock:
        key = user_id or session_id
        if key not in _stats["by_user"]:
            _stats["by_user"][key] = {"user_id": user_id, "session_id": session_id, "cost_usd": 0.0, "tokens": 0, "requests": 0}
        u = _stats["by_user"][key]
        u["cost_usd"] += cost
        u["tokens"] += tokens
        u["requests"] += 1


async def call_aoai(deployment: str, messages: list[dict], tools: list | None = None) -> tuple:
    """Returns (response, retries_count)"""
    with tracer.start_as_current_span("llm-call") as span:
        span.set_attribute("llm.model", deployment)
        span.set_attribute("llm.message_count", len(messages))

        # [NEW] Prompt content logging
        user_msgs = [m["content"] for m in messages if m.get("role") == "user" and isinstance(m.get("content"), str)]
        prompt_text = " | ".join(user_msgs)[:PROMPT_LOG_MAX_LEN]
        span.set_attribute("llm.prompt", prompt_text)

        # [NEW] Retry with rate limit tracking
        retries = 0
        last_error = None
        start = time.time()

        for attempt in range(MAX_RETRIES + 1):
            try:
                kwargs = {"model": deployment, "messages": messages, "temperature": 0.7}
                if tools:
                    kwargs["tools"] = tools
                response = await aoai.chat.completions.create(**kwargs)
                break
            except RateLimitError as e:
                retries += 1
                last_error = e
                rate_limit_counter.add(1, {"llm.model": deployment, "agent.type": AGENT_TYPE})
                retry_counter.add(1, {"llm.model": deployment, "reason": "rate_limit"})
                with _stats_lock:
                    _stats["total_rate_limits"] += 1
                    _stats["total_retries"] += 1

                retry_after = float(e.response.headers.get("retry-after", 2 ** attempt))
                span.add_event("rate_limit_hit", {"attempt": attempt + 1, "retry_after": retry_after})
                logger.warning(f"Rate limit 429 — retry {attempt + 1}/{MAX_RETRIES}, wait {retry_after}s",
                               extra={"model": deployment, "retry_after": retry_after})

                if attempt < MAX_RETRIES:
                    await asyncio.sleep(retry_after)
                else:
                    raise
            except APIStatusError as e:
                retries += 1
                last_error = e
                retry_counter.add(1, {"llm.model": deployment, "reason": f"status_{e.status_code}"})
                with _stats_lock:
                    _stats["total_retries"] += 1
                span.add_event("api_error", {"attempt": attempt + 1, "status_code": e.status_code})
                logger.warning(f"API error {e.status_code} — retry {attempt + 1}/{MAX_RETRIES}",
                               extra={"model": deployment, "status_code": e.status_code})
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise

        duration = time.time() - start
        usage = response.usage
        call_cost = _calc_cost(deployment, usage.prompt_tokens, usage.completion_tokens)

        # Span attributes
        span.set_attribute("llm.prompt_tokens", usage.prompt_tokens)
        span.set_attribute("llm.completion_tokens", usage.completion_tokens)
        span.set_attribute("llm.total_tokens", usage.total_tokens)
        span.set_attribute("llm.duration", round(duration, 3))
        span.set_attribute("llm.cost_usd", call_cost)
        span.set_attribute("llm.retries", retries)

        # [NEW] Response content logging
        resp_content = response.choices[0].message.content or ""
        span.set_attribute("llm.response", resp_content[:PROMPT_LOG_MAX_LEN])
        if response.choices[0].message.tool_calls:
            tc_names = [tc.function.name for tc in response.choices[0].message.tool_calls]
            span.set_attribute("llm.tool_calls", ",".join(tc_names))

        # Metrics
        llm_call_duration.record(duration, {"llm.model": deployment, "agent.type": AGENT_TYPE})
        token_usage_counter.add(usage.prompt_tokens, {"llm.model": deployment, "type": "prompt"})
        token_usage_counter.add(usage.completion_tokens, {"llm.model": deployment, "type": "completion"})
        request_token_histogram.record(usage.total_tokens, {"llm.model": deployment, "agent.type": AGENT_TYPE})
        cost_counter.add(call_cost, {"llm.model": deployment, "agent.type": AGENT_TYPE})

        # Stats
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
            "retries": retries,
        })
        return response, retries


async def execute_tool_call(tool_call, params: dict | None = None) -> str:
    fn_name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)
    url = SUB_AGENT_URLS.get(fn_name)

    if not url:
        return f"Error: unknown tool '{fn_name}'"

    with tracer.start_as_current_span("sub-agent-call") as span:
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
                logger.info("Sub-agent call completed", extra={"tool": fn_name, "status": "success"})
                return result
        except Exception as e:
            span.set_attribute("sub_agent.status", "error")
            span.set_attribute("error", True)
            logger.error("Sub-agent call failed", extra={"tool": fn_name, "error": str(e)})
            return f"Error calling {fn_name}: {e}"


# ──────────────────────────── FastAPI ───────────────────────────
app = FastAPI(title=f"AI Agent - {AGENT_TYPE}")
FastAPIInstrumentor.instrument_app(app)


@app.post("/run")
async def run_agent(req: AgentRequest):
    with tracer.start_as_current_span("agent-run") as span:
        span.set_attribute("agent.type", AGENT_TYPE)
        span.set_attribute("request.query", req.query[:200])
        if req.params:
            for k, v in req.params.items():
                span.set_attribute(f"param.{k}", v)
        if req.model_override:
            span.set_attribute("llm.model_override", req.model_override)
        agent_run_counter.add(1, {"agent.type": AGENT_TYPE})
        logger.info("Agent run started", extra={"agent_type": AGENT_TYPE, "query": req.query[:100], "params": req.params})

        try:
            if AGENT_TYPE == "orchestrator":
                return await _run_orchestrator(req, span)
            else:
                return await _run_sub_agent(req, span)
        except Exception as e:
            span.set_attribute("error", True)
            agent_error_counter.add(1, {"agent.type": AGENT_TYPE, "error.type": type(e).__name__})
            logger.error("Agent run failed", extra={"agent_type": AGENT_TYPE, "error": str(e)})
            raise HTTPException(status_code=500, detail=str(e))


async def _run_orchestrator(req: AgentRequest, span) -> AgentResponse:
    deployment = req.model_override or profile["deployment"]
    messages = [
        {"role": "system", "content": profile["system_prompt"]},
        {"role": "user", "content": req.query},
    ]

    response, retries1 = await call_aoai(deployment, messages, tools=ORCHESTRATOR_TOOLS)
    choice = response.choices[0]

    if not choice.message.tool_calls:
        p, c = response.usage.prompt_tokens, response.usage.completion_tokens
        cost = _calc_cost(deployment, p, c)
        _track_user_cost(req.params, cost, p + c)
        return AgentResponse(
            agent_type="orchestrator", model=deployment,
            result=choice.message.content or "",
            tokens={"prompt": p, "completion": c},
            cost_usd=cost, retries=retries1,
        )

    messages.append(choice.message.model_dump())
    agents_called = []
    for tc in choice.message.tool_calls:
        agents_called.append(tc.function.name)
        result = await execute_tool_call(tc, params=req.params)
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    span.set_attribute("orchestrator.agents_called", ",".join(agents_called))

    final, retries2 = await call_aoai(deployment, messages)
    total_p = response.usage.prompt_tokens + final.usage.prompt_tokens
    total_c = response.usage.completion_tokens + final.usage.completion_tokens
    cost = _calc_cost(deployment, total_p, total_c)
    _track_user_cost(req.params, cost, total_p + total_c)
    return AgentResponse(
        agent_type="orchestrator", model=deployment,
        result=final.choices[0].message.content or "",
        tokens={"prompt": total_p, "completion": total_c},
        cost_usd=cost, retries=retries1 + retries2,
    )


async def _run_sub_agent(req: AgentRequest, span) -> AgentResponse:
    deployment = req.model_override or profile["deployment"]

    # [NEW] Cache check
    cached = cache_get(deployment, req.query)
    if cached:
        result_text, meta = cached
        cache_hit_counter.add(1, {"agent.type": AGENT_TYPE})
        with _stats_lock:
            _stats["total_cache_hits"] += 1
        span.set_attribute("cache.hit", True)
        span.set_attribute("cache.original_cost_usd", meta.get("cost_usd", 0))
        logger.info("Cache hit", extra={"agent_type": AGENT_TYPE, "query": req.query[:80]})
        _track_user_cost(req.params, 0, 0)
        return AgentResponse(
            agent_type=AGENT_TYPE, model=deployment,
            result=result_text,
            tokens=meta.get("tokens"), cost_usd=0.0, cached=True,
        )

    cache_miss_counter.add(1, {"agent.type": AGENT_TYPE})
    with _stats_lock:
        _stats["total_cache_misses"] += 1
    span.set_attribute("cache.hit", False)

    messages = [
        {"role": "system", "content": profile["system_prompt"]},
        {"role": "user", "content": req.query},
    ]
    if req.context:
        messages.insert(1, {"role": "user", "content": f"Context:\n{req.context}"})

    response, retries = await call_aoai(deployment, messages)
    p, c = response.usage.prompt_tokens, response.usage.completion_tokens
    cost = _calc_cost(deployment, p, c)
    result_text = response.choices[0].message.content or ""

    # [NEW] Cache store
    cache_set(deployment, req.query, result_text, {"tokens": {"prompt": p, "completion": c}, "cost_usd": cost})

    _track_user_cost(req.params, cost, p + c)
    return AgentResponse(
        agent_type=AGENT_TYPE, model=deployment,
        result=result_text,
        tokens={"prompt": p, "completion": c},
        cost_usd=cost, retries=retries,
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
            "total_retries": _stats["total_retries"],
            "total_rate_limits": _stats["total_rate_limits"],
            "cache": {
                "hits": _stats["total_cache_hits"],
                "misses": _stats["total_cache_misses"],
                "hit_rate": round(_stats["total_cache_hits"] / max(_stats["total_cache_hits"] + _stats["total_cache_misses"], 1) * 100, 1),
                "size": len(_cache),
                "ttl_seconds": CACHE_TTL,
            },
            "by_model": {
                model: {
                    **data, "cost_usd": round(data["cost_usd"], 6),
                    "avg_tokens_per_call": round((data["prompt_tokens"] + data["completion_tokens"]) / max(data["calls"], 1), 1),
                }
                for model, data in _stats["by_model"].items()
            },
            "by_user": dict(_stats["by_user"]),
            "pricing_per_1m_tokens": PRICING,
        }


@app.get("/cache/clear")
def clear_cache():
    with _cache_lock:
        count = len(_cache)
        _cache.clear()
    return {"cleared": count}
