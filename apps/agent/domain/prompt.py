"""프롬프트 해석 결과 — 불변 값 객체."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PromptResolution:
    """프롬프트 매니저가 반환하는 해석 결과.

    Attributes:
        system_prompt: 시스템 프롬프트 텍스트
        version: 선택된 프롬프트 버전 (예: "1.0", "1.1", "fallback")
        variant: 선택 경로 ("active" | "ab-1.0" 등)
        source: 출처 ("yaml" | "fallback")
        content_hash: 프롬프트 콘텐츠 해시 (변경 감지용, 8자)
    """

    system_prompt: str
    version: str
    variant: str
    source: str
    content_hash: str
