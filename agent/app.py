"""
FastAPI 애플리케이션 — HTTP 계층만 담당한다.

비즈니스 로직은 application/use_cases.py에 있으며,
container.py가 모든 의존성을 조립해 use_case를 주입한다.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from application.use_cases import QuotaExceededError
from config import AGENT_TYPE, SERVICE_NAME, CACHE_TTL, USE_DAPR, DAPR_HTTP_PORT, DAPR_PUBSUB_NAME, DAPR_TOPIC
from container import build_use_case
from infrastructure.sub_agent_invoker import close_http_client
from models import AgentRequest, AgentResponse
from otel_setup import logger, shutdown_providers
from stats import get_stats

# ──────────────────────────── 앱 시작 시 UseCase 조립 ───────────
_use_case = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _use_case
    _use_case = build_use_case()
    logger.info("Agent starting", extra={"agent_type": AGENT_TYPE, "service": SERVICE_NAME})
    yield
    logger.info("Agent shutting down", extra={"agent_type": AGENT_TYPE})
    await close_http_client()
    shutdown_providers()
    from config import USE_LANGFUSE
    if USE_LANGFUSE:
        from langfuse import Langfuse
        Langfuse().flush()


app = FastAPI(title=f"AI Agent - {AGENT_TYPE}", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


# ──────────────────────────── Dapr Pub/Sub ─────────────────────
@app.get("/dapr/subscribe")
def dapr_subscribe():
    if not USE_DAPR:
        return []
    return [{"pubsubname": DAPR_PUBSUB_NAME, "topic": DAPR_TOPIC, "route": "/events"}]


@app.post("/events")
async def handle_event(request: Request):
    event = await request.json()
    data = event.get("data", {})
    logger.info("Dapr event received", extra={
        "event_type": data.get("event_type"),
        "source_agent": data.get("agent_type"),
        "source_service": data.get("service"),
    })
    return {"status": "SUCCESS"}


# ──────────────────────────── 핵심 엔드포인트 ───────────────────
@app.post("/run")
async def run_agent(req: AgentRequest) -> AgentResponse:
    try:
        return await _use_case.execute(req)
    except QuotaExceededError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Agent run failed", extra={"agent_type": AGENT_TYPE, "error": str(e)})
        raise HTTPException(status_code=500, detail="Internal server error")


# ──────────────────────────── 보조 엔드포인트 ───────────────────
@app.get("/health")
def health():
    return {"status": "ok", "agent_type": AGENT_TYPE, "service": SERVICE_NAME}


@app.get("/stats")
async def stats():
    from cache import cache_size
    size = await cache_size()
    data = await get_stats(cache_size=size, cache_ttl=CACHE_TTL)
    return {"agent_type": AGENT_TYPE, "service": SERVICE_NAME, **data}


@app.post("/cache/clear")
async def clear_cache():
    from cache import cache_clear
    count = await cache_clear()
    return {"cleared": count}
