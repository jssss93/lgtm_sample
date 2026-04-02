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


def test_cache_key_deterministic():
    from cache import _cache_key

    key1 = _cache_key("gpt-4.1", "Hello World")
    key2 = _cache_key("gpt-4.1", "Hello World")
    assert key1 == key2


def test_cache_key_case_insensitive():
    from cache import _cache_key

    key1 = _cache_key("gpt-4.1", "Hello World")
    key2 = _cache_key("gpt-4.1", "hello world")
    assert key1 == key2


def test_cache_key_strips_whitespace():
    from cache import _cache_key

    key1 = _cache_key("gpt-4.1", "hello")
    key2 = _cache_key("gpt-4.1", "  hello  ")
    assert key1 == key2


def test_cache_key_different_models():
    from cache import _cache_key

    key1 = _cache_key("gpt-4.1", "hello")
    key2 = _cache_key("gpt-4.1-mini", "hello")
    assert key1 != key2


def test_cache_get_set():
    from cache import cache_get, cache_set, cache_clear, _cache

    async def _test():
        await cache_clear()

        # 캐시 미스
        result = await cache_get("gpt-4.1", "test query")
        assert result is None

        # 캐시 저장
        await cache_set("gpt-4.1", "test query", "answer", {"tokens": {"prompt": 10, "completion": 5}})

        # 캐시 히트
        result = await cache_get("gpt-4.1", "test query")
        assert result is not None
        text, meta = result
        assert text == "answer"
        assert meta["tokens"]["prompt"] == 10

        # 대소문자 무관 캐시 히트
        result = await cache_get("gpt-4.1", "TEST QUERY")
        assert result is not None

        await cache_clear()

    asyncio.run(_test())


def test_cache_max_size():
    from cache import cache_get, cache_set, cache_clear, _cache
    import config

    original_max = config.CACHE_MAX_SIZE

    async def _test():
        await cache_clear()
        config.CACHE_MAX_SIZE = 3

        # reimport to get the module-level reference updated
        import cache as cache_mod
        old_max = cache_mod.CACHE_MAX_SIZE
        # Temporarily patch
        import types
        cache_mod.CACHE_MAX_SIZE = 3

        await cache_set("m", "q1", "r1", {})
        await cache_set("m", "q2", "r2", {})
        await cache_set("m", "q3", "r3", {})
        await cache_set("m", "q4", "r4", {})  # q1이 evict 됨

        assert await cache_get("m", "q1") is None
        assert await cache_get("m", "q4") is not None

        cache_mod.CACHE_MAX_SIZE = old_max
        await cache_clear()
        config.CACHE_MAX_SIZE = original_max

    asyncio.run(_test())


def test_cache_clear():
    from cache import cache_set, cache_clear, cache_size

    async def _test():
        await cache_set("m", "q1", "r1", {})
        await cache_set("m", "q2", "r2", {})
        count = await cache_clear()
        assert count >= 2
        assert await cache_size() == 0

    asyncio.run(_test())


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


def test_check_quota_no_params():
    from stats import check_quota

    async def _test():
        result = await check_quota(None)
        assert result is None
        result = await check_quota({})
        assert result is None

    asyncio.run(_test())


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
