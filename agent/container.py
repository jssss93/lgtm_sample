"""
DI 컨테이너 — 모든 컴포넌트를 한 곳에서 조립한다.

앱 시작 시 build_use_case()를 한 번 호출하면 사용 준비가 된
SubAgentUseCase 또는 OrchestratorUseCase 인스턴스가 반환된다.
"""
import json
import os

import urllib.request

from config import (
    AGENT_TYPE, AGENT_PROFILES, ORCHESTRATOR_TOOLS,
    USE_DAPR, USE_LANGFUSE,
    DAPR_HTTP_PORT, DAPR_STATE_STORE, DAPR_SECRET_STORE,
    DAPR_PUBSUB_NAME, DAPR_TOPIC,
    CACHE_TTL, CACHE_MAX_SIZE, SERVICE_NAME,
)
from domain.ports import CacheBackend, LLMProvider, MetricsRecorder, EventPublisher
from infrastructure.cache_memory import MemoryCacheBackend
from infrastructure.cache_dapr import DaprCacheBackend
from infrastructure.llm_aoai import AzureOpenAIProvider
from infrastructure.metrics_otel import OTelMetricsRecorder
from infrastructure.events import DaprEventPublisher, NoOpEventPublisher
from infrastructure.sub_agent_invoker import SubAgentInvoker
from application.use_cases import SubAgentUseCase, OrchestratorUseCase


# ──────────────────────────── 자격증명 로드 ─────────────────────
def _fetch_dapr_secret(key: str) -> dict:
    """시작 시 Dapr Secret Store에서 시크릿을 동기 로드."""
    url = f"http://localhost:{DAPR_HTTP_PORT}/v1.0/secrets/{DAPR_SECRET_STORE}/{key}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


def _get_aoai_credentials() -> tuple[str, str, str]:
    """AOAI 자격증명을 Dapr Secret Store 또는 환경변수에서 가져온다."""
    if USE_DAPR:
        secrets = _fetch_dapr_secret("azure-openai")
        if secrets:
            return (
                secrets.get("endpoint", ""),
                secrets.get("api-key", ""),
                secrets.get("api-version", "2024-12-01-preview"),
            )
    return (
        os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        os.getenv("AZURE_OPENAI_API_KEY", ""),
        os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
    )


# ──────────────────────────── 컴포넌트 빌더 ─────────────────────
def _build_cache() -> CacheBackend:
    if USE_DAPR:
        return DaprCacheBackend(DAPR_HTTP_PORT, DAPR_STATE_STORE, CACHE_TTL)
    return MemoryCacheBackend(CACHE_TTL, CACHE_MAX_SIZE)


def _build_llm() -> LLMProvider:
    endpoint, api_key, api_version = _get_aoai_credentials()
    return AzureOpenAIProvider(endpoint, api_key, api_version)


def _build_metrics() -> MetricsRecorder:
    from otel_setup import (
        agent_run_counter, agent_error_counter,
        cache_hit_counter, cache_miss_counter, quota_reject_counter,
    )
    return OTelMetricsRecorder(
        agent_run_counter=agent_run_counter,
        agent_error_counter=agent_error_counter,
        cache_hit_counter=cache_hit_counter,
        cache_miss_counter=cache_miss_counter,
        quota_reject_counter=quota_reject_counter,
    )


def _build_events() -> EventPublisher:
    if USE_DAPR:
        return DaprEventPublisher(DAPR_HTTP_PORT, DAPR_PUBSUB_NAME, DAPR_TOPIC, AGENT_TYPE, SERVICE_NAME)
    return NoOpEventPublisher()


# ──────────────────────────── 진입점 ────────────────────────────
def build_use_case():
    """앱 시작 시 한 번 호출. 조립된 UseCase 인스턴스 반환."""
    cache = _build_cache()
    llm = _build_llm()
    metrics = _build_metrics()
    events = _build_events()
    profile = AGENT_PROFILES.get(AGENT_TYPE, AGENT_PROFILES["search"])

    if AGENT_TYPE == "orchestrator":
        return OrchestratorUseCase(
            profile=profile,
            tools=ORCHESTRATOR_TOOLS,
            llm=llm,
            metrics=metrics,
            events=events,
            sub_agent_invoker=SubAgentInvoker(),
        )
    return SubAgentUseCase(
        agent_type=AGENT_TYPE,
        profile=profile,
        cache=cache,
        llm=llm,
        metrics=metrics,
        events=events,
    )
