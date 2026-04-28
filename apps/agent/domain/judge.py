"""LLM-as-judge 스코어링 — 에이전트 타입별 루브릭 기반 평가."""
import json
import re
from dataclasses import dataclass
from typing import Optional

from domain.ports import LLMProvider

# ──────────────────────────── 루브릭 ────────────────────────────
# 에이전트 역할에 맞는 채점 기준을 명시한다.
# 범용 "관련성/정확성" 기준 대신 실제 운영 목적에 맞는 항목을 정의.

RUBRICS: dict[str, str] = {
    "search": (
        "You are evaluating a **search agent** response.\n"
        "This agent answers factual/knowledge questions (target: under 200 words).\n\n"
        "Score each dimension 0–10 (integers only):\n"
        "- relevance: Does it directly answer the specific factual question?\n"
        "- accuracy: Are the stated facts verifiable and trustworthy? "
        "Penalize vague, unverifiable, or hedging responses.\n"
        "- completeness: Are key facts included without omission? "
        "Penalize if a critical aspect of the question goes unanswered.\n"
        "- overall: Holistic judgment for a fact-lookup assistant. "
        "Weight accuracy most heavily."
    ),
    "summarizer": (
        "You are evaluating a **summarization agent** response.\n"
        "This agent summarizes input text (target: under 150 words).\n\n"
        "Score each dimension 0–10 (integers only):\n"
        "- relevance: Does the summary stay on-topic with the source material?\n"
        "- accuracy: Is every fact in the summary faithful to the source? "
        "Penalize any detail not present in the original (hallucination).\n"
        "- completeness: Are all key points preserved without losing critical information?\n"
        "- overall: Holistic judgment for a summarization task. "
        "Weight accuracy (faithfulness) most heavily."
    ),
    "coder": (
        "You are evaluating a **code generation agent** response.\n"
        "This agent writes or reviews code with explanations.\n\n"
        "Score each dimension 0–10 (integers only):\n"
        "- relevance: Does the code address the exact task requested?\n"
        "- accuracy: Is the code syntactically valid and logically correct? "
        "Penalize bugs, security issues (SQL injection, hardcoded secrets, "
        "unhandled exceptions), or broken logic.\n"
        "- completeness: Is a brief explanation included? Are obvious edge cases handled?\n"
        "- overall: Holistic judgment for a coding assistant. "
        "Heavily penalize code with security vulnerabilities."
    ),
    "orchestrator": (
        "You are evaluating an **orchestrator agent** response.\n"
        "This agent routes queries to sub-agents and synthesizes their results.\n\n"
        "Score each dimension 0–10 (integers only):\n"
        "- relevance: Does the final synthesized answer address the original question?\n"
        "- accuracy: Is the synthesized content factually sound and internally consistent? "
        "Penalize contradictions between sub-results.\n"
        "- completeness: Does the answer integrate all necessary sub-results "
        "into a coherent whole without leaving gaps?\n"
        "- overall: Holistic judgment of orchestration and synthesis quality."
    ),
    "default": (
        "You are evaluating an AI assistant response.\n\n"
        "Score each dimension 0–10 (integers only):\n"
        "- relevance: How directly does the response address the question?\n"
        "- accuracy: How factually sound and trustworthy is the content?\n"
        "- completeness: How thoroughly does it cover what was asked?\n"
        "- overall: Holistic quality judgment."
    ),
}

_SYSTEM_SUFFIX = (
    "\n\nRespond ONLY with a valid JSON object — no markdown, no explanation:\n"
    '{"relevance": <int>, "accuracy": <int>, "completeness": <int>, "overall": <int>}'
)

_JSON_PATTERN = re.compile(r"\{[^{}]+\}")


# ──────────────────────────── 값 객체 ────────────────────────────

@dataclass(frozen=True)
class JudgeScores:
    relevance: float
    accuracy: float
    completeness: float
    overall: float

    def as_dict(self) -> dict:
        return {
            "relevance": self.relevance,
            "accuracy": self.accuracy,
            "completeness": self.completeness,
            "overall": self.overall,
        }


# ──────────────────────────── 스코어러 ───────────────────────────

class JudgeScorer:
    """LLM-as-judge 평가기. 에이전트 타입별 루브릭으로 0~10점 채점."""

    def __init__(self, llm: LLMProvider, deployment: str):
        self._llm = llm
        self._deployment = deployment

    async def score(
        self,
        agent_type: str,
        query: str,
        response: str,
    ) -> Optional[JudgeScores]:
        """채점 실패 시 None 반환 — 호출자는 항상 None 체크 필요."""
        rubric = RUBRICS.get(agent_type, RUBRICS["default"])
        messages = [
            {"role": "system", "content": rubric + _SYSTEM_SUFFIX},
            {
                "role": "user",
                "content": (
                    f"[QUESTION]\n{query[:500]}\n\n"
                    f"[RESPONSE]\n{response[:1000]}\n\n"
                    "JSON only:"
                ),
            },
        ]
        try:
            resp, _ = await self._llm.complete(self._deployment, messages)
            raw = resp.choices[0].message.content or ""
            return parse_scores(raw)
        except Exception:
            return None


# ──────────────────────────── 파싱 유틸 (테스트 가능) ───────────

def parse_scores(raw: str) -> Optional[JudgeScores]:
    """JSON 파싱 → 0~10 clamp → JudgeScores. 실패 시 None.

    1차: 전체 텍스트 직접 파싱
    2차: 정규식으로 JSON 객체 추출 후 파싱
    """
    candidates = [raw.strip()] + _JSON_PATTERN.findall(raw)
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            return JudgeScores(
                relevance=_clamp(data["relevance"]),
                accuracy=_clamp(data["accuracy"]),
                completeness=_clamp(data["completeness"]),
                overall=_clamp(data["overall"]),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return None


def _clamp(value) -> float:
    return float(max(0.0, min(10.0, float(int(value)))))
