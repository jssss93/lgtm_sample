"""도메인 값 객체 — 외부 의존성 없는 순수 Python."""
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMTokens:
    prompt: int
    completion: int

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    def to_dict(self) -> dict:
        return {"prompt": self.prompt, "completion": self.completion}


@dataclass(frozen=True)
class LLMCallResult:
    content: str
    tokens: LLMTokens
    cost_usd: float
    retries: int
    model: str


@dataclass(frozen=True)
class CachedResult:
    content: str
    tokens: LLMTokens
    cost_usd: float


@dataclass
class UserQuota:
    """사용자 쿼터 정책. 0 = 무제한."""
    token_quota: int = 0
    cost_quota_usd: float = 0.0

    def check_tokens(self, used: int) -> str | None:
        if self.token_quota > 0 and used >= self.token_quota:
            return f"Token quota exceeded ({used}/{self.token_quota})"
        return None

    def check_cost(self, used_usd: float) -> str | None:
        if self.cost_quota_usd > 0 and used_usd >= self.cost_quota_usd:
            return f"Cost quota exceeded (${used_usd:.4f}/${self.cost_quota_usd})"
        return None
