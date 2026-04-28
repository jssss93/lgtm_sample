"""응답 품질 점수 계산 — 외부 의존 없는 순수 도메인 함수."""
import re

_ERROR_KEYWORDS = re.compile(
    r"i don'?t know|i cannot|i can'?t|unable to|sorry|죄송|모르겠|잘 모|알 수 없",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.!?。다요]\s*$")


def compute_quality_score(text: str) -> float:
    """응답 텍스트 기반 휴리스틱 품질 점수 (0.0~1.0).

    감점 기준:
    - 응답 길이 < 50자  : -0.4
    - 응답 길이 < 100자 : -0.2
    - 에러 키워드 포함  : -0.3 per match (최대 -0.5)
    - 문장 미완결        : -0.2
    """
    stripped = text.strip()
    if not stripped:
        return 0.0

    score = 1.0

    if len(stripped) < 50:
        score -= 0.4
    elif len(stripped) < 100:
        score -= 0.2

    matches = len(_ERROR_KEYWORDS.findall(stripped))
    score -= min(matches * 0.3, 0.5)

    if stripped and not _SENTENCE_END.search(stripped):
        score -= 0.2

    return max(round(score, 2), 0.0)
