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
    recorder.record_quality_score("search", "gpt-4.1-mini", 0.85)


# ──────────────────────────── Quality Score 테스트 ───────────────
def test_compute_quality_score_high():
    from domain.quality import compute_quality_score as _compute_quality_score

    text = "Azure OpenAI는 GPT-4.1 모델을 API로 제공하는 서비스입니다. REST API 또는 Python SDK로 호출할 수 있습니다."
    assert _compute_quality_score(text) >= 0.8


def test_compute_quality_score_short():
    from domain.quality import compute_quality_score as _compute_quality_score

    assert _compute_quality_score("모르겠습니다.") < 0.5


def test_compute_quality_score_error_keyword():
    from domain.quality import compute_quality_score as _compute_quality_score

    text = "I cannot provide an answer to that question due to policy restrictions."
    score = _compute_quality_score(text)
    assert score < 0.8


def test_compute_quality_score_incomplete():
    from domain.quality import compute_quality_score as _compute_quality_score

    text = "이 기능은 다음과 같이 작동하며 여러 단계를 거쳐서 처리되는데 그 과정에서"
    score = _compute_quality_score(text)
    assert score <= 0.8


def test_compute_quality_score_floor():
    from domain.quality import compute_quality_score as _compute_quality_score

    assert _compute_quality_score("") == 0.0          # 빈 응답 → 즉시 0
    assert _compute_quality_score("?") < 0.8           # 1자 → 낮은 점수
    assert 0.0 <= _compute_quality_score("?") <= 1.0  # 범위 보장


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

    # scorer는 system_prompt 없는 특수 프로필 (judge 호출용)
    agent_profiles = {k: v for k, v in AGENT_PROFILES.items() if k != "scorer"}
    for name, profile in agent_profiles.items():
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


# ──────────────────────────── PromptResolution 값 객체 테스트 ────
def test_prompt_resolution_immutable():
    from domain.prompt import PromptResolution
    import pytest

    res = PromptResolution(
        system_prompt="test", version="1.0", variant="active",
        source="yaml", content_hash="abcd1234",
    )
    assert res.version == "1.0"
    assert res.source == "yaml"
    with pytest.raises((AttributeError, TypeError)):
        res.version = "2.0"  # type: ignore[misc]


def test_prompt_resolution_fields():
    from domain.prompt import PromptResolution

    res = PromptResolution(
        system_prompt="You are a search agent.", version="1.1",
        variant="ab-1.1", source="yaml", content_hash="12345678",
    )
    assert res.system_prompt == "You are a search agent."
    assert res.variant == "ab-1.1"
    assert res.content_hash == "12345678"


# ──────────────────────────── YamlPromptManager 테스트 ───────────
def test_prompt_manager_load_yaml(tmp_path):
    """YAML 파일에서 프롬프트 로드 및 resolve 동작 확인."""
    import yaml
    from infrastructure.prompt_manager import YamlPromptManager
    from infrastructure.metrics_otel import NoOpMetricsRecorder

    # YAML 파일 생성
    prompt_data = {
        "versions": {
            "1.0": {"system_prompt": "You are a search agent v1."},
            "1.1": {"system_prompt": "You are an advanced search agent v1.1."},
        },
        "active": "1.1",
        "ab_test": {"enabled": False, "variants": []},
    }
    (tmp_path / "search.yaml").write_text(yaml.dump(prompt_data))

    manager = YamlPromptManager(
        prompts_dir=str(tmp_path),
        fallback_profiles={"search": {"system_prompt": "fallback"}},
        metrics=NoOpMetricsRecorder(),
    )
    res = manager.resolve("search")
    assert res.version == "1.1"
    assert "advanced search" in res.system_prompt
    assert res.source == "yaml"
    assert res.variant == "active"


def test_prompt_manager_fallback():
    """YAML 파일 없을 때 fallback 프로필 사용 확인."""
    from infrastructure.prompt_manager import YamlPromptManager
    from infrastructure.metrics_otel import NoOpMetricsRecorder

    manager = YamlPromptManager(
        prompts_dir="/nonexistent/path",
        fallback_profiles={"search": {"system_prompt": "fallback prompt"}},
        metrics=NoOpMetricsRecorder(),
    )
    res = manager.resolve("search")
    assert res.version == "fallback"
    assert res.source == "fallback"
    assert res.system_prompt == "fallback prompt"


def test_prompt_manager_ab_sticky(tmp_path):
    """A/B 테스트 sticky assignment: 동일 subject_id → 동일 variant 선택."""
    import yaml
    from infrastructure.prompt_manager import YamlPromptManager
    from infrastructure.metrics_otel import NoOpMetricsRecorder

    prompt_data = {
        "versions": {
            "1.0": {"system_prompt": "v1.0"},
            "1.1": {"system_prompt": "v1.1"},
        },
        "active": "1.0",
        "ab_test": {
            "enabled": True,
            "variants": [
                {"version": "1.0", "weight": 50},
                {"version": "1.1", "weight": 50},
            ],
        },
    }
    (tmp_path / "search.yaml").write_text(yaml.dump(prompt_data))

    manager = YamlPromptManager(
        prompts_dir=str(tmp_path),
        fallback_profiles={},
        metrics=NoOpMetricsRecorder(),
    )
    # 동일 subject_id는 항상 같은 결과
    res1 = manager.resolve("search", subject_id="user-42")
    res2 = manager.resolve("search", subject_id="user-42")
    assert res1.version == res2.version
    assert res1.variant == res2.variant
    assert res1.variant.startswith("ab-")


def test_prompt_manager_invalid_yaml_keeps_previous(tmp_path):
    """잘못된 YAML 로드 시 이전 설정 유지."""
    import yaml
    from infrastructure.prompt_manager import YamlPromptManager
    from infrastructure.metrics_otel import NoOpMetricsRecorder

    valid_data = {
        "versions": {"1.0": {"system_prompt": "valid prompt"}},
        "active": "1.0",
    }
    yaml_path = tmp_path / "search.yaml"
    yaml_path.write_text(yaml.dump(valid_data))

    manager = YamlPromptManager(
        prompts_dir=str(tmp_path),
        fallback_profiles={},
        metrics=NoOpMetricsRecorder(),
    )
    assert manager.resolve("search").system_prompt == "valid prompt"

    # 잘못된 YAML 작성
    yaml_path.write_text("not: valid: yaml: [")
    from pathlib import Path
    manager._load_file(Path(yaml_path))

    # 이전 설정이 유지되어야 함
    assert manager.resolve("search").system_prompt == "valid prompt"


def test_prompt_manager_get_info(tmp_path):
    """get_info() 메타데이터 반환 확인."""
    import yaml
    from infrastructure.prompt_manager import YamlPromptManager
    from infrastructure.metrics_otel import NoOpMetricsRecorder

    prompt_data = {
        "versions": {"1.0": {"system_prompt": "test"}, "1.1": {"system_prompt": "test2"}},
        "active": "1.0",
        "ab_test": {"enabled": False},
    }
    (tmp_path / "search.yaml").write_text(yaml.dump(prompt_data))

    manager = YamlPromptManager(
        prompts_dir=str(tmp_path),
        fallback_profiles={"coder": {"system_prompt": "coder fallback"}},
        metrics=NoOpMetricsRecorder(),
    )
    info = manager.get_info()
    assert "search" in info
    assert info["search"]["active_version"] == "1.0"
    assert "1.0" in info["search"]["available_versions"]
    assert "1.1" in info["search"]["available_versions"]
    assert info["search"]["source"] == "yaml"
    # fallback 프로필도 표시
    assert "coder" in info
    assert info["coder"]["source"] == "fallback"


# ──────────────────────────── 캐시 키 prompt_version 테스트 ──────
def test_cache_key_with_prompt_version():
    from infrastructure.cache_memory import _cache_key

    key_no_ver = _cache_key("gpt-4.1", "hello")
    key_v1 = _cache_key("gpt-4.1", "hello", "1.0")
    key_v2 = _cache_key("gpt-4.1", "hello", "1.1")

    # 버전이 다르면 캐시 키도 달라야 함
    assert key_no_ver != key_v1
    assert key_v1 != key_v2
    # 동일 버전은 동일 키
    assert key_v1 == _cache_key("gpt-4.1", "hello", "1.0")


def test_cache_prompt_version_isolation():
    """프롬프트 버전이 다른 캐시 항목은 격리되어야 함."""
    from infrastructure.cache_memory import MemoryCacheBackend

    async def _test():
        backend = MemoryCacheBackend(ttl_seconds=300, max_size=100)

        await backend.set("gpt-4.1", "hello", "answer-v1", {}, prompt_version="1.0")
        await backend.set("gpt-4.1", "hello", "answer-v2", {}, prompt_version="1.1")

        r1 = await backend.get("gpt-4.1", "hello", prompt_version="1.0")
        r2 = await backend.get("gpt-4.1", "hello", prompt_version="1.1")
        r_none = await backend.get("gpt-4.1", "hello", prompt_version="2.0")

        assert r1 is not None and r1[0] == "answer-v1"
        assert r2 is not None and r2[0] == "answer-v2"
        assert r_none is None

    asyncio.run(_test())


# ──────────────────────────── NoOp 메트릭 확장 테스트 ────────────
def test_noop_metrics_prompt_methods():
    from infrastructure.metrics_otel import NoOpMetricsRecorder

    recorder = NoOpMetricsRecorder()
    # 새 메서드 호출 시 예외 없이 통과해야 함
    recorder.record_prompt_reload("search", "1.0")
    recorder.record_prompt_selection("search", "1.0", "active")
    recorder.record_quality_score("search", "gpt-4.1-mini", 0.85, prompt_version="1.0")
    recorder.record_judge_scores("search", "gpt-4.1", 8.0, 7.5, 9.0, 8.5, prompt_version="1.1")
