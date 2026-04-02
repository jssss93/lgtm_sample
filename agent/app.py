import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from config import (
    AGENT_TYPE, SERVICE_NAME, AGENT_PROFILES, CACHE_TTL,
    ORCHESTRATOR_TOOLS,
)
from otel_setup import (
    tracer, logger, shutdown_providers,
    agent_run_counter, agent_error_counter,
    cache_hit_counter, cache_miss_counter, quota_reject_counter,
)
from cache import cache_get, cache_set, cache_clear, cache_size
from stats import (
    calc_cost, track_user_cost, track_cache_hit, track_cache_miss,
    check_quota, get_stats,
)
from llm import call_aoai, execute_tool_call, close_http_client
from models import AgentRequest, AgentResponse

profile = AGENT_PROFILES.get(AGENT_TYPE, AGENT_PROFILES["search"])


# ──────────────────────────── Lifespan (Graceful Shutdown) ──────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Agent starting", extra={"agent_type": AGENT_TYPE, "service": SERVICE_NAME})
    yield
    logger.info("Agent shutting down", extra={"agent_type": AGENT_TYPE})
    await close_http_client()
    shutdown_providers()


app = FastAPI(title=f"AI Agent - {AGENT_TYPE}", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


# ──────────────────────────── Routes ────────────────────────────
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
        logger.info("Agent run started", extra={
            "agent_type": AGENT_TYPE, "query": req.query[:100], "params": req.params,
        })

        # Quota 확인
        quota_error = await check_quota(req.params)
        if quota_error:
            quota_reject_counter.add(1, {"agent.type": AGENT_TYPE})
            span.set_attribute("quota.rejected", True)
            logger.warning("Quota exceeded", extra={"agent_type": AGENT_TYPE, "reason": quota_error})
            raise HTTPException(status_code=429, detail=quota_error)

        try:
            if AGENT_TYPE == "orchestrator":
                return await _run_orchestrator(req, span)
            else:
                return await _run_sub_agent(req, span)
        except HTTPException:
            raise
        except Exception as e:
            span.set_attribute("error", True)
            agent_error_counter.add(1, {"agent.type": AGENT_TYPE, "error.type": type(e).__name__})
            logger.error("Agent run failed", extra={"agent_type": AGENT_TYPE, "error": str(e)})
            raise HTTPException(status_code=500, detail="Internal server error")


async def _run_orchestrator(req: AgentRequest, span) -> AgentResponse:
    deployment = req.model_override or profile["deployment"]
    messages = [
        {"role": "system", "content": profile["system_prompt"]},
    ]
    # context 필드 활용
    if req.context:
        messages.append({"role": "user", "content": f"Context:\n{req.context}"})
    messages.append({"role": "user", "content": req.query})

    response, retries1 = await call_aoai(deployment, messages, tools=ORCHESTRATOR_TOOLS)
    choice = response.choices[0]

    if not choice.message.tool_calls:
        p, c = response.usage.prompt_tokens, response.usage.completion_tokens
        cost = calc_cost(deployment, p, c)
        await track_user_cost(req.params, cost, p + c)
        return AgentResponse(
            agent_type="orchestrator", model=deployment,
            result=choice.message.content or "",
            tokens={"prompt": p, "completion": c},
            cost_usd=cost, retries=retries1,
        )

    messages.append(choice.message.model_dump())

    # Sub-agent 호출을 병렬로 실행
    tool_calls = choice.message.tool_calls
    results = await asyncio.gather(
        *[execute_tool_call(tc, params=req.params) for tc in tool_calls]
    )
    agents_called = []
    for tc, result in zip(tool_calls, results):
        agents_called.append(tc.function.name)
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    span.set_attribute("orchestrator.agents_called", ",".join(agents_called))

    final, retries2 = await call_aoai(deployment, messages)
    total_p = response.usage.prompt_tokens + final.usage.prompt_tokens
    total_c = response.usage.completion_tokens + final.usage.completion_tokens
    cost = calc_cost(deployment, total_p, total_c)
    await track_user_cost(req.params, cost, total_p + total_c)
    return AgentResponse(
        agent_type="orchestrator", model=deployment,
        result=final.choices[0].message.content or "",
        tokens={"prompt": total_p, "completion": total_c},
        cost_usd=cost, retries=retries1 + retries2,
    )


async def _run_sub_agent(req: AgentRequest, span) -> AgentResponse:
    deployment = req.model_override or profile["deployment"]

    # Cache 확인
    cached = await cache_get(deployment, req.query)
    if cached:
        result_text, meta = cached
        cache_hit_counter.add(1, {"agent.type": AGENT_TYPE})
        await track_cache_hit()
        span.set_attribute("cache.hit", True)
        span.set_attribute("cache.original_cost_usd", meta.get("cost_usd", 0))
        logger.info("Cache hit", extra={"agent_type": AGENT_TYPE, "query": req.query[:80]})
        await track_user_cost(req.params, 0, 0)
        return AgentResponse(
            agent_type=AGENT_TYPE, model=deployment,
            result=result_text,
            tokens=meta.get("tokens"), cost_usd=0.0, cached=True,
        )

    cache_miss_counter.add(1, {"agent.type": AGENT_TYPE})
    await track_cache_miss()
    span.set_attribute("cache.hit", False)

    messages = [
        {"role": "system", "content": profile["system_prompt"]},
    ]
    if req.context:
        messages.append({"role": "user", "content": f"Context:\n{req.context}"})
    messages.append({"role": "user", "content": req.query})

    response, retries = await call_aoai(deployment, messages)
    p, c = response.usage.prompt_tokens, response.usage.completion_tokens
    cost = calc_cost(deployment, p, c)
    result_text = response.choices[0].message.content or ""

    # Cache 저장
    await cache_set(deployment, req.query, result_text, {
        "tokens": {"prompt": p, "completion": c}, "cost_usd": cost,
    })

    await track_user_cost(req.params, cost, p + c)
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
async def stats():
    size = await cache_size()
    data = await get_stats(cache_size=size, cache_ttl=CACHE_TTL)
    return {"agent_type": AGENT_TYPE, "service": SERVICE_NAME, **data}


@app.post("/cache/clear")
async def clear_cache():
    count = await cache_clear()
    return {"cleared": count}
