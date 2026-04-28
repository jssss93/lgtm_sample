"""LLM 품질 평가 회귀 테스트.

실행: make test-eval  (LLM 호출 없음 — 골든셋 + mock 기반)

커버리지:
1. 골든셋 휴리스틱 점수 임계값 검증  — compute_quality_score()
2. judge parse_scores() 파싱 로직     — 정상 / 마크다운 포함 / 실패 케이스
3. 루브릭 완결성                       — 모든 에이전트 타입에 루브릭 존재
4. 부정 케이스 점수 천장               — 저품질 응답이 높은 점수를 얻지 않음
"""
import json
import sys
import os
from pathlib import Path

import pytest

# ── 경로 설정 ─────────────────────────────────────────────────────
AGENT_DIR = Path(__file__).parent.parent / "agent"
GOLDEN_DIR = Path(__file__).parent / "golden"
sys.path.insert(0, str(AGENT_DIR))


# ════════════════════════════════════════════════════════════════
# 1. 골든셋 로드 헬퍼
# ════════════════════════════════════════════════════════════════

def _load_golden(agent_type: str) -> list[dict]:
    path = GOLDEN_DIR / f"{agent_type}.json"
    with open(path) as f:
        data = json.load(f)
    return data["cases"]


# ════════════════════════════════════════════════════════════════
# 2. 골든셋 휴리스틱 점수 검증
# ════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("agent_type", ["search", "summarizer", "coder", "orchestrator"])
def test_golden_positive_cases_heuristic_score(agent_type):
    """골든 응답(긍정 케이스)은 min_quality_score 이상이어야 한다."""
    from domain.quality import compute_quality_score

    cases = [c for c in _load_golden(agent_type) if not c.get("is_negative")]
    assert cases, f"{agent_type}: 긍정 케이스가 없음"

    for case in cases:
        score = compute_quality_score(case["golden_response"])
        assert score >= case["min_quality_score"], (
            f"[{case['id']}] score={score:.2f} < min={case['min_quality_score']}\n"
            f"  response: {case['golden_response'][:100]}"
        )


@pytest.mark.parametrize("agent_type", ["search", "summarizer", "coder", "orchestrator"])
def test_golden_negative_cases_heuristic_score(agent_type):
    """부정 케이스 중 heuristic_detectable=true인 케이스는 0.5 이하여야 한다.

    heuristic_detectable=false: 환각·보안 취약점처럼 텍스트 형태만으로는
    감지 불가한 케이스 — LLM-as-judge 전용이므로 휴리스틱 임계값 검사를 건너뜀.
    """
    from domain.quality import compute_quality_score

    cases = [
        c for c in _load_golden(agent_type)
        if c.get("is_negative") and c.get("heuristic_detectable", True)
    ]
    if not cases:
        pytest.skip(f"{agent_type}: 휴리스틱 감지 가능한 부정 케이스 없음")

    for case in cases:
        score = compute_quality_score(case["golden_response"])
        assert score <= 0.5, (
            f"[{case['id']}] 저품질 응답이 score={score:.2f}로 너무 높음\n"
            f"  response: {case['golden_response'][:100]}"
        )


# ════════════════════════════════════════════════════════════════
# 3. judge parse_scores() 파싱 로직
# ════════════════════════════════════════════════════════════════

def test_parse_scores_clean_json():
    from domain.judge import parse_scores

    raw = '{"relevance": 8, "accuracy": 7, "completeness": 9, "overall": 8}'
    scores = parse_scores(raw)
    assert scores is not None
    assert scores.relevance == 8.0
    assert scores.accuracy == 7.0
    assert scores.completeness == 9.0
    assert scores.overall == 8.0


def test_parse_scores_with_markdown_wrapper():
    """LLM이 ```json ... ``` 으로 감싸 반환하는 경우."""
    from domain.judge import parse_scores

    raw = '```json\n{"relevance": 6, "accuracy": 5, "completeness": 7, "overall": 6}\n```'
    scores = parse_scores(raw)
    assert scores is not None
    assert scores.overall == 6.0


def test_parse_scores_with_explanation_text():
    """LLM이 설명을 앞에 붙인 경우 — 정규식으로 JSON 추출."""
    from domain.judge import parse_scores

    raw = 'Here is my evaluation:\n{"relevance": 9, "accuracy": 8, "completeness": 8, "overall": 8}\nEnd.'
    scores = parse_scores(raw)
    assert scores is not None
    assert scores.relevance == 9.0


def test_parse_scores_clamp_out_of_range():
    """0~10 범위를 벗어난 값은 clamp 처리."""
    from domain.judge import parse_scores

    raw = '{"relevance": 15, "accuracy": -2, "completeness": 7, "overall": 8}'
    scores = parse_scores(raw)
    assert scores is not None
    assert scores.relevance == 10.0
    assert scores.accuracy == 0.0


def test_parse_scores_missing_key_returns_none():
    """필수 키 누락 시 None 반환."""
    from domain.judge import parse_scores

    raw = '{"relevance": 8, "accuracy": 7}'  # completeness, overall 없음
    assert parse_scores(raw) is None


def test_parse_scores_invalid_json_returns_none():
    from domain.judge import parse_scores

    assert parse_scores("not json at all") is None
    assert parse_scores("") is None
    assert parse_scores("{}") is None


# ════════════════════════════════════════════════════════════════
# 4. 루브릭 완결성
# ════════════════════════════════════════════════════════════════

def test_rubrics_exist_for_all_agent_types():
    """모든 에이전트 타입에 루브릭이 정의되어 있어야 한다."""
    from domain.judge import RUBRICS
    from config import AGENT_PROFILES

    required = set(AGENT_PROFILES.keys()) - {"scorer"}
    for agent_type in required:
        assert agent_type in RUBRICS or "default" in RUBRICS, (
            f"{agent_type}에 대한 루브릭이 없음"
        )


def test_rubrics_contain_all_dimensions():
    """각 루브릭에 4개 차원(relevance/accuracy/completeness/overall)이 언급되어야 한다."""
    from domain.judge import RUBRICS

    for agent_type, rubric in RUBRICS.items():
        for dim in ["relevance", "accuracy", "completeness", "overall"]:
            assert dim in rubric.lower(), (
                f"루브릭 '{agent_type}'에 '{dim}' 차원이 없음"
            )


# ════════════════════════════════════════════════════════════════
# 5. JudgeScorer 모의 테스트 (LLM 호출 없음)
# ════════════════════════════════════════════════════════════════

class _MockLLMResponse:
    class _Choice:
        class _Message:
            content = '{"relevance": 8, "accuracy": 7, "completeness": 9, "overall": 8}'
        message = _Message()
    choices = [_Choice()]
    class usage:
        prompt_tokens = 100
        completion_tokens = 20


class _MockLLMProvider:
    async def complete(self, deployment, messages, tools=None):
        return _MockLLMResponse(), 0


@pytest.mark.asyncio
async def test_judge_scorer_returns_scores():
    """JudgeScorer가 mock LLM으로 점수를 정상 반환하는지 확인."""
    from domain.judge import JudgeScorer

    scorer = JudgeScorer(llm=_MockLLMProvider(), deployment="gpt-4.1-mini")
    scores = await scorer.score("search", "What is Kubernetes?", "Kubernetes is ...")
    assert scores is not None
    assert scores.overall == 8.0
    assert scores.relevance == 8.0


@pytest.mark.asyncio
async def test_judge_scorer_handles_llm_failure():
    """LLM 오류 시 None 반환 — 예외 전파 없음."""
    from domain.judge import JudgeScorer

    class _FailingLLM:
        async def complete(self, *args, **kwargs):
            raise RuntimeError("LLM unavailable")

    scorer = JudgeScorer(llm=_FailingLLM(), deployment="gpt-4.1-mini")
    result = await scorer.score("search", "q", "a")
    assert result is None


# ════════════════════════════════════════════════════════════════
# 6. 샘플링 로직 (순수 함수로 직접 테스트)
# ════════════════════════════════════════════════════════════════

import random as _random


def _should_sample(rate: float) -> bool:
    """use_cases._should_sample과 동일 구현 — otel 없이 테스트 가능."""
    return rate > 0.0 and _random.random() < rate


def test_should_sample_zero_rate():
    assert _should_sample(0.0) is False


def test_should_sample_full_rate():
    assert all(_should_sample(1.0) for _ in range(20))


def test_should_sample_respects_rate():
    _random.seed(42)
    results = [_should_sample(0.3) for _ in range(1000)]
    sampled = sum(results)
    # 10% 오차 허용 (300 ± 30)
    assert 270 <= sampled <= 330, f"샘플링 비율 이상: {sampled}/1000"
