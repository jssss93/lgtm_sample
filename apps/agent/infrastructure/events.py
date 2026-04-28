"""이벤트 발행 인프라 — Dapr Pub/Sub 및 NoOp 구현."""
import httpx

from domain.ports import EventPublisher


class DaprEventPublisher(EventPublisher):
    def __init__(
        self,
        dapr_port: int,
        pubsub_name: str,
        topic: str,
        agent_type: str,
        service_name: str,
    ):
        self._url = f"http://localhost:{dapr_port}/v1.0/publish/{pubsub_name}/{topic}"
        self._agent_type = agent_type
        self._service_name = service_name

    async def publish(self, event_type: str, data: dict) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    self._url,
                    json={
                        "event_type": event_type,
                        "agent_type": self._agent_type,
                        "service": self._service_name,
                        **data,
                    },
                )
        except Exception:
            pass  # best-effort


class NoOpEventPublisher(EventPublisher):
    """Dapr 미사용 모드 또는 테스트용."""

    async def publish(self, event_type: str, data: dict) -> None:
        pass
