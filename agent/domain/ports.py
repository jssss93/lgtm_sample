"""포트(추상 인터페이스) — 고수준 정책이 저수준 구현에 의존하지 않도록 한다."""
from abc import ABC, abstractmethod
from typing import Optional, Tuple, List, Dict


class CacheBackend(ABC):
    """캐시 저장소 포트. 인메모리/Dapr/Redis 등 다양한 백엔드로 교체 가능."""

    @abstractmethod
    async def get(self, deployment: str, query: str) -> Optional[Tuple[str, dict]]:
        """(result_text, meta) 반환. 미스 시 None."""

    @abstractmethod
    async def set(self, deployment: str, query: str, result: str, meta: dict) -> None:
        """결과를 캐시에 저장."""

    @abstractmethod
    async def clear(self) -> int:
        """전체 캐시 삭제. 삭제된 항목 수 반환."""

    @abstractmethod
    async def size(self) -> int:
        """현재 캐시 크기. 지원 불가 시 -1."""


class LLMProvider(ABC):
    """LLM 호출 포트. Azure OpenAI / Anthropic / 로컬 모델 등으로 교체 가능."""

    @abstractmethod
    async def complete(
        self,
        deployment: str,
        messages: List[Dict],
        tools: Optional[List] = None,
    ) -> Tuple[object, int]:
        """(raw_response, retries_count) 반환."""


class MetricsRecorder(ABC):
    """메트릭 발행 포트. OTel / NoOp / 테스트용 스파이 등으로 교체 가능."""

    @abstractmethod
    def record_agent_run(self, agent_type: str) -> None: ...

    @abstractmethod
    def record_agent_error(self, agent_type: str, error_type: str) -> None: ...

    @abstractmethod
    def record_cache_hit(self, agent_type: str) -> None: ...

    @abstractmethod
    def record_cache_miss(self, agent_type: str) -> None: ...

    @abstractmethod
    def record_quota_reject(self, agent_type: str) -> None: ...


class EventPublisher(ABC):
    """도메인 이벤트 발행 포트. Dapr Pub/Sub / NoOp 등으로 교체 가능."""

    @abstractmethod
    async def publish(self, event_type: str, data: dict) -> None: ...
