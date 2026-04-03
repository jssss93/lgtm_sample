"""
{{ cookiecutter.description }}
Agent Type: {{ cookiecutter.agent_type }}
Model: {{ cookiecutter.model }}

이 파일은 agent-template에서 자동 생성되었습니다.
비즈니스 로직만 수정하세요. 인프라(Dapr, OTel)는 플랫폼이 관리합니다.
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from config import AGENT_TYPE, SERVICE_NAME, CACHE_TTL
from config import USE_DAPR, DAPR_HTTP_PORT, DAPR_PUBSUB_NAME, DAPR_TOPIC
from otel_setup import (
    tracer, logger, shutdown_providers,
    agent_run_counter, agent_error_counter,
    cache_hit_counter, cache_miss_counter, quota_reject_counter,
)
from cache import cache_get, cache_set, cache_clear, cache_size
from stats import calc_cost, track_user_cost, track_cache_hit, track_cache_miss, check_quota, get_stats
from llm import call_aoai, close_http_client
from models import AgentRequest, AgentResponse

import httpx

PROFILE = {
    "deployment": "{{ cookiecutter.model }}",
    "system_prompt": "{{ cookiecutter.system_prompt }}",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Agent starting", extra={"agent_type": AGENT_TYPE, "service": SERVICE_NAME})
    yield
    logger.info("Agent shutting down", extra={"agent_type": AGENT_TYPE})
    await close_http_client()
    shutdown_providers()


app = FastAPI(title="{{ cookiecutter.description }}", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


# ──────────────────────────── Dapr Pub/Sub ─────────────────────
async def publish_event(event_type: str, data: dict):
    if not USE_DAPR:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"http://localhost:{DAPR_HTTP_PORT}/v1.0/publish/{DAPR_PUBSUB_NAME}/{DAPR_TOPIC}",
                json={"event_type": event_type, "agent_type": AGENT_TYPE, "service": SERVICE_NAME, **data},
            )
    except Exception:
        pass


@app.get("/dapr/subscribe")
def dapr_subscribe():
    if not USE_DAPR:
        return []
    return [{"pubsubname": DAPR_PUBSUB_NAME, "topic": DAPR_TOPIC, "route": "/events"}]


@app.post("/events")
async def handle_event(request: Request):
    return {"status": "SUCCESS"}


# ──────────────────────────── Main Route ───────────────────────
@app.post("/run")
async def run_agent(req: AgentRequest):
    with tracer.start_as_current_span("agent-run") as span:
        span.set_attribute("agent.type", AGENT_TYPE)
        span.set_attribute("request.query", req.query[:200])
        if req.params:
            for k, v in req.params.items():
                span.set_attribute(f"param.{k}", v)
        agent_run_counter.add(1, {"agent.type": AGENT_TYPE})

        quota_error = await check_quota(req.params)
        if quota_error:
            quota_reject_counter.add(1, {"agent.type": AGENT_TYPE})
            raise HTTPException(status_code=429, detail=quota_error)

        try:
            deployment = req.model_override or PROFILE["deployment"]

            # Cache 확인
            cached = await cache_get(deployment, req.query)
            if cached:
                result_text, meta = cached
                cache_hit_counter.add(1, {"agent.type": AGENT_TYPE})
                await track_cache_hit()
                span.set_attribute("cache.hit", True)
                await track_user_cost(req.params, 0, 0)
                result = AgentResponse(
                    agent_type=AGENT_TYPE, model=deployment,
                    result=result_text, tokens=meta.get("tokens"),
                    cost_usd=0.0, cached=True,
                )
                await publish_event("agent.run.completed", {"cached": True})
                return result

            cache_miss_counter.add(1, {"agent.type": AGENT_TYPE})
            await track_cache_miss()
            span.set_attribute("cache.hit", False)

            # ════════════════════════════════════════════════
            # 비즈니스 로직: 여기를 수정하세요
            # ════════════════════════════════════════════════
            messages = [
                {"role": "system", "content": PROFILE["system_prompt"]},
            ]
            if req.context:
                messages.append({"role": "user", "content": f"Context:\n{req.context}"})
            messages.append({"role": "user", "content": req.query})

            response, retries = await call_aoai(deployment, messages)
            # ════════════════════════════════════════════════

            p, c = response.usage.prompt_tokens, response.usage.completion_tokens
            cost = calc_cost(deployment, p, c)
            result_text = response.choices[0].message.content or ""

            await cache_set(deployment, req.query, result_text, {
                "tokens": {"prompt": p, "completion": c}, "cost_usd": cost,
            })
            await track_user_cost(req.params, cost, p + c)

            result = AgentResponse(
                agent_type=AGENT_TYPE, model=deployment,
                result=result_text,
                tokens={"prompt": p, "completion": c},
                cost_usd=cost, retries=retries,
            )
            await publish_event("agent.run.completed", {
                "model": deployment, "cost_usd": cost, "cached": False,
            })
            return result

        except HTTPException:
            raise
        except Exception as e:
            agent_error_counter.add(1, {"agent.type": AGENT_TYPE, "error.type": type(e).__name__})
            raise HTTPException(status_code=500, detail="Internal server error")


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
