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
        quality_score_histogram,
        judge_relevance_histogram,
        judge_accuracy_histogram,
        judge_completeness_histogram,
        judge_overall_histogram,
        prompt_reload_counter=None,
        prompt_selection_counter=None,
    ):
        self._agent_run = agent_run_counter
        self._agent_error = agent_error_counter
        self._cache_hit = cache_hit_counter
        self._cache_miss = cache_miss_counter
        self._quota_reject = quota_reject_counter
        self._quality_score = quality_score_histogram
        self._judge_relevance = judge_relevance_histogram
        self._judge_accuracy = judge_accuracy_histogram
        self._judge_completeness = judge_completeness_histogram
        self._judge_overall = judge_overall_histogram
        self._prompt_reload = prompt_reload_counter
        self._prompt_selection = prompt_selection_counter

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

    def record_quality_score(self, agent_type: str, model: str, score: float, prompt_version: str = "") -> None:
        labels = {"agent.type": agent_type, "llm.model": model}
        if prompt_version:
            labels["prompt.version"] = prompt_version
        self._quality_score.record(score, labels)

    def record_judge_scores(
        self,
        agent_type: str,
        model: str,
        relevance: float,
        accuracy: float,
        completeness: float,
        overall: float,
        prompt_version: str = "",
    ) -> None:
        labels = {"agent.type": agent_type, "llm.model": model}
        if prompt_version:
            labels["prompt.version"] = prompt_version
        self._judge_relevance.record(relevance, labels)
        self._judge_accuracy.record(accuracy, labels)
        self._judge_completeness.record(completeness, labels)
        self._judge_overall.record(overall, labels)

    def record_prompt_reload(self, agent_type: str, version: str) -> None:
        if self._prompt_reload:
            self._prompt_reload.add(1, {"agent.type": agent_type, "prompt.version": version})

    def record_prompt_selection(self, agent_type: str, version: str, variant: str) -> None:
        if self._prompt_selection:
            self._prompt_selection.add(1, {"agent.type": agent_type, "prompt.version": version, "prompt.variant": variant})


class NoOpMetricsRecorder(MetricsRecorder):
    """테스트용 — 아무것도 하지 않는 구현."""

    def record_agent_run(self, agent_type: str) -> None: pass
    def record_agent_error(self, agent_type: str, error_type: str) -> None: pass
    def record_cache_hit(self, agent_type: str) -> None: pass
    def record_cache_miss(self, agent_type: str) -> None: pass
    def record_quota_reject(self, agent_type: str) -> None: pass
    def record_quality_score(self, agent_type: str, model: str, score: float, prompt_version: str = "") -> None: pass
    def record_judge_scores(
        self, agent_type: str, model: str,
        relevance: float, accuracy: float, completeness: float, overall: float,
        prompt_version: str = "",
    ) -> None: pass
    def record_prompt_reload(self, agent_type: str, version: str) -> None: pass
    def record_prompt_selection(self, agent_type: str, version: str, variant: str) -> None: pass
