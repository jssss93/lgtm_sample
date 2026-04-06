"""OTel 메트릭 레코더 및 NoOp 구현."""
from domain.ports import MetricsRecorder


class OTelMetricsRecorder(MetricsRecorder):
    """otel_setup의 counter들을 주입받아 발행."""

    def __init__(
        self,
        agent_run_counter,
        agent_error_counter,
        cache_hit_counter,
        cache_miss_counter,
        quota_reject_counter,
    ):
        self._agent_run = agent_run_counter
        self._agent_error = agent_error_counter
        self._cache_hit = cache_hit_counter
        self._cache_miss = cache_miss_counter
        self._quota_reject = quota_reject_counter

    def record_agent_run(self, agent_type: str) -> None:
        self._agent_run.add(1, {"agent.type": agent_type})

    def record_agent_error(self, agent_type: str, error_type: str) -> None:
        self._agent_error.add(1, {"agent.type": agent_type, "error.type": error_type})

    def record_cache_hit(self, agent_type: str) -> None:
        self._cache_hit.add(1, {"agent.type": agent_type})

    def record_cache_miss(self, agent_type: str) -> None:
        self._cache_miss.add(1, {"agent.type": agent_type})

    def record_quota_reject(self, agent_type: str) -> None:
        self._quota_reject.add(1, {"agent.type": agent_type})


class NoOpMetricsRecorder(MetricsRecorder):
    """테스트용 — 아무것도 하지 않는 구현."""

    def record_agent_run(self, agent_type: str) -> None: pass
    def record_agent_error(self, agent_type: str, error_type: str) -> None: pass
    def record_cache_hit(self, agent_type: str) -> None: pass
    def record_cache_miss(self, agent_type: str) -> None: pass
    def record_quota_reject(self, agent_type: str) -> None: pass
