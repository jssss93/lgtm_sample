"""
단위 테스트 — 핵심 로직 (외부 서비스 불필요)
실행: python -m pytest tests/test_unit.py -v
"""
import asyncio
import sys
import os

# agent 모듈을 임포트할 수 있도록 경로 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))

# OTel 초기화를 방지하기 위해 환경변수 설정
os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://dummy.openai.azure.com/")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "dummy-key")


# ──────────────────────────── 비용 계산 ─────────────────────────
def test_calc_cost():
    from stats import calc_cost

    # gpt-4.1: prompt=2.00/1M, completion=8.00/1M
    cost = calc_cost("gpt-4.1", 1000, 500)
    expected = round(1000 * 2.00 / 1_000_000 + 500 * 8.00 / 1_000_000, 6)
    assert cost == expected

    # gpt-4.1-mini: prompt=0.40/1M, completion=1.60/1M
    cost = calc_cost("gpt-4.1-mini", 10000, 2000)
    expected = round(10000 * 0.40 / 1_000_000 + 2000 * 1.60 / 1_000_000, 6)
    assert cost == expected

    # 알 수 없는 모델 → 비용 0
    cost = calc_cost("unknown-model", 1000, 1000)
    assert cost == 0.0


def test_calc_cost_zero_tokens():
    from stats import calc_cost

    cost = calc_cost("gpt-4.1", 0, 0)
    assert cost == 0.0


# ──────────────────────────── 캐시 키 (인프라 레이어 직접 테스트) ─
def test_cache_key_deterministic():
    from infrastructure.cache_memory import _cache_key

    key1 = _cache_key("gpt-4.1", "Hello World")
    key2 = _cache_key("gpt-4.1", "Hello World")
    assert key1 == key2


def test_cache_key_case_insensitive():
    from infrastructure.cache_memory import _cache_key

    key1 = _cache_key("gpt-4.1", "Hello World")
    key2 = _cache_key("gpt-4.1", "hello world")
    assert key1 == key2


def test_cache_key_strips_whitespace():
    from infrastructure.cache_memory import _cache_key

    key1 = _cache_key("gpt-4.1", "hello")
    key2 = _cache_key("gpt-4.1", "  hello  ")
    assert key1 == key2


def test_cache_key_different_models():
    from infrastructure.cache_memory import _cache_key

    key1 = _cache_key("gpt-4.1", "hello")
    key2 = _cache_key("gpt-4.1-mini", "hello")
    assert key1 != key2


# ──────────────────────────── MemoryCacheBackend 단위 테스트 ────
def test_cache_get_set():
    from infrastructure.cache_memory import MemoryCacheBackend

    async def _test():
        backend = MemoryCacheBackend(ttl_seconds=300, max_size=100)

        # 캐시 미스
        result = await backend.get("gpt-4.1", "test query")
        assert result is None

        # 캐시 저장
        await backend.set("gpt-4.1", "test query", "answer", {"tokens": {"prompt": 10, "completion": 5}})

        # 캐시 히트
        result = await backend.get("gpt-4.1", "test query")
        assert result is not None
        text, meta = result
        assert text == "answer"
        assert meta["tokens"]["prompt"] == 10

        # 대소문자 무관 캐시 히트
        result = await backend.get("gpt-4.1", "TEST QUERY")
        assert result is not None

        # clear
        count = await backend.clear()
        assert count >= 1
        assert await backend.size() == 0

    asyncio.run(_test())


def test_cache_max_size():
    from infrastructure.cache_memory import MemoryCacheBackend

    async def _test():
        # max_size=3으로 직접 생성해 LRU 방출 테스트
        backend = MemoryCacheBackend(ttl_seconds=300, max_size=3)

        await backend.set("m", "q1", "r1", {})
        await backend.set("m", "q2", "r2", {})
        await backend.set("m", "q3", "r3", {})
        await backend.set("m", "q4", "r4", {})  # q1이 evict 됨

        assert await backend.get("m", "q1") is None
        assert await backend.get("m", "q4") is not None

    asyncio.run(_test())


def test_cache_clear():
    from infrastructure.cache_memory import MemoryCacheBackend

    async def _test():
        backend = MemoryCacheBackend(ttl_seconds=300, max_size=100)
        await backend.set("m", "q1", "r1", {})
        await backend.set("m", "q2", "r2", {})
        count = await backend.clear()
        assert count >= 2
        assert await backend.size() == 0

    asyncio.run(_test())


def test_cache_ttl_expiry():
    """TTL 만료 시 캐시 미스 반환 확인."""
    import time
    from infrastructure.cache_memory import MemoryCacheBackend, _cache_key
    from collections import OrderedDict

    async def _test():
        backend = MemoryCacheBackend(ttl_seconds=1, max_size=100)
        # 만료된 항목을 수동으로 삽입
        key = _cache_key("gpt-4.1", "expired query")
        backend._cache[key] = ("old result", time.time() - 10, {})  # 10초 전
        result = await backend.get("gpt-4.1", "expired query")
        assert result is None

    asyncio.run(_test())


# ──────────────────────────── 도메인 값 객체 테스트 ─────────────
def test_llm_tokens_total():
    from domain.value_objects import LLMTokens

    tokens = LLMTokens(prompt=100, completion=50)
    assert tokens.total == 150
    assert tokens.to_dict() == {"prompt": 100, "completion": 50}


def test_llm_tokens_immutable():
    from domain.value_objects import LLMTokens
    import pytest

    tokens = LLMTokens(prompt=100, completion=50)
    with pytest.raises((AttributeError, TypeError)):
        tokens.prompt = 999  # type: ignore[misc]


def test_user_quota_unlimited():
    from domain.value_objects import UserQuota

    quota = UserQuota(token_quota=0, cost_quota_usd=0.0)
    assert quota.check_tokens(9_999_999) is None
    assert quota.check_cost(9_999_999.0) is None


def test_user_quota_token_exceeded():
    from domain.value_objects import UserQuota

    quota = UserQuota(token_quota=1000)
    assert quota.check_tokens(999) is None
    assert quota.check_tokens(1000) is not None
    assert "1000" in quota.check_tokens(1000)


def test_user_quota_cost_exceeded():
    from domain.value_objects import UserQuota

    quota = UserQuota(cost_quota_usd=1.0)
    assert quota.check_cost(0.99) is None
    assert quota.check_cost(1.0) is not None


# ──────────────────────────── MetricsRecorder NoOp 테스트 ────────
def test_noop_metrics_recorder():
    from infrastructure.metrics_otel import NoOpMetricsRecorder

    recorder = NoOpMetricsRecorder()
    # 예외 없이 호출되어야 함
    recorder.record_agent_run("search")
    recorder.record_agent_error("search", "ValueError")
    recorder.record_cache_hit("search")
    recorder.record_cache_miss("search")
    recorder.record_quota_reject("search")


# ──────────────────────────── HTTP 모델 테스트 ───────────────────
def test_models_request():
    from models import AgentRequest

    req = AgentRequest(query="test")
    assert req.query == "test"
    assert req.context is None
    assert req.params is None
    assert req.model_override is None


def test_models_request_with_all_fields():
    from models import AgentRequest

    req = AgentRequest(
        query="test",
        context="some context",
        params={"user_id": "u1", "session_id": "s1"},
        model_override="gpt-4.1-mini",
    )
    assert req.context == "some context"
    assert req.params["user_id"] == "u1"
    assert req.model_override == "gpt-4.1-mini"


def test_models_response():
    from models import AgentResponse

    resp = AgentResponse(
        agent_type="search",
        model="gpt-4.1-mini",
        result="hello",
        tokens={"prompt": 10, "completion": 5},
        cost_usd=0.001,
    )
    assert resp.agent_type == "search"
    assert resp.cached is False
    assert resp.retries == 0


# ──────────────────────────── 쿼터 테스트 ───────────────────────
def test_check_quota_no_params():
    from stats import check_quota

    async def _test():
        result = await check_quota(None)
        assert result is None
        result = await check_quota({})
        assert result is None

    asyncio.run(_test())


# ──────────────────────────── 설정 테스트 ───────────────────────
def test_config_agent_profiles():
    from config import AGENT_PROFILES

    assert "orchestrator" in AGENT_PROFILES
    assert "search" in AGENT_PROFILES
    assert "summarizer" in AGENT_PROFILES
    assert "coder" in AGENT_PROFILES

    for name, profile in AGENT_PROFILES.items():
        assert "deployment" in profile
        assert "system_prompt" in profile
        assert len(profile["system_prompt"]) > 0


def test_config_pricing():
    from config import PRICING

    for model, prices in PRICING.items():
        assert "prompt" in prices
        assert "completion" in prices
        assert prices["prompt"] >= 0
        assert prices["completion"] >= 0


# ──────────────────────────── 포트 인터페이스 검증 ─────────────
def test_cache_backend_is_abstract():
    """CacheBackend는 추상 클래스이므로 직접 인스턴스화 불가."""
    import pytest
    from domain.ports import CacheBackend

    with pytest.raises(TypeError):
        CacheBackend()  # type: ignore[abstract]


def test_memory_cache_implements_port():
    """MemoryCacheBackend가 CacheBackend 포트를 완전히 구현하는지 확인."""
    from domain.ports import CacheBackend
    from infrastructure.cache_memory import MemoryCacheBackend

    backend = MemoryCacheBackend(ttl_seconds=300, max_size=100)
    assert isinstance(backend, CacheBackend)
