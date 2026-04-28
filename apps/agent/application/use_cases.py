"""애플리케이션 유스 케이스 — 비즈니스 로직만, 인프라 직접 의존 없음."""
import asyncio
import random
from typing import Optional

from domain.ports import CacheBackend, LLMProvider, MetricsRecorder, EventPublisher, PromptProvider
from domain.quality import compute_quality_score
from domain.judge import JudgeScorer
from models import AgentRequest, AgentResponse
from otel_setup import tracer, logger
from stats import calc_cost, track_user_cost, track_cache_hit, track_cache_miss, check_quota


class QuotaExceededError(Exception):
    pass


def _should_sample(rate: float) -> bool:
    return rate > 0.0 and random.random() < rate


class SubAgentUseCase:
    """캐시 조회 → LLM 호출 → 캐시 저장 흐름을 담당하는 단일 책임 유스 케이스."""

    def __init__(
        self,
        agent_type: str,
        profile: dict,
        cache: CacheBackend,
        llm: LLMProvider,
        metrics: MetricsRecorder,
        events: EventPublisher,
        prompt_provider: Optional[PromptProvider] = None,
        judge: Optional[JudgeScorer] = None,
        judge_sample_rate: float = 0.0,
    ):
        self._agent_type = agent_type
        self._profile = profile
        self._cache = cache
        self._llm = llm
        self._metrics = metrics
        self._events = events
        self._prompt_provider = prompt_provider
        self._judge = judge
        self._judge_sample_rate = judge_sample_rate

    async def execute(self, req: AgentRequest) -> AgentResponse:
        # Quota 확인 (사이드이펙트 없음, 순수 읽기)
        quota_error = await check_quota(req.params)
        if quota_error:
            self._metrics.record_quota_reject(self._agent_type)
            raise QuotaExceededError(quota_error)

        self._metrics.record_agent_run(self._agent_type)

        with tracer.start_as_current_span("agent-run") as span:
            span.set_attribute("agent.type", self._agent_type)
            span.set_attribute("request.query", req.query[:200])
            if req.params:
                for k, v in req.params.items():
                    span.set_attribute(f"param.{k}", str(v))
            if req.model_override:
                span.set_attribute("llm.model_override", req.model_override)

            deployment = req.model_override or self._profile["deployment"]

            # ── 프롬프트 해석 (캐시 조회보다 먼저) ──────────────────
            subject_id = None
            if req.params:
                subject_id = str(req.params.get("user_id") or req.params.get("session_id") or "")
            if self._prompt_provider:
                resolution = self._prompt_provider.resolve(self._agent_type, subject_id or None)
            else:
                fallback_prompt = self._profile.get("system_prompt", "")
                from domain.prompt import PromptResolution
                import hashlib
                resolution = PromptResolution(
                    system_prompt=fallback_prompt, version="fallback",
                    variant="fallback", source="fallback",
                    content_hash=hashlib.md5(fallback_prompt.encode()).hexdigest()[:8],
                )
            prompt_version = resolution.version
            span.set_attribute("prompt.version", prompt_version)
            span.set_attribute("prompt.variant", resolution.variant)
            span.set_attribute("prompt.source", resolution.source)

            # ── 캐시 조회 (prompt_version 포함) ─────────────────────
            cached = await self._cache.get(deployment, req.query, prompt_version=prompt_version)
            if cached:
                result_text, meta = cached
                self._metrics.record_cache_hit(self._agent_type)
                await track_cache_hit()
                span.set_attribute("cache.hit", True)
                span.set_attribute("cache.original_cost_usd", meta.get("cost_usd", 0))
                logger.info("Cache hit", extra={"agent_type": self._agent_type, "query": req.query[:80]})
                await track_user_cost(req.params, 0, 0)
                return AgentResponse(
                    agent_type=self._agent_type, model=deployment,
                    result=result_text,
                    tokens=meta.get("tokens"), cost_usd=0.0, cached=True,
                    prompt_version=prompt_version,
                )

            # ── 캐시 미스 → LLM 호출 ────────────────────────────────
            self._metrics.record_cache_miss(self._agent_type)
            await track_cache_miss()
            span.set_attribute("cache.hit", False)

            messages = [{"role": "system", "content": resolution.system_prompt}]
            if req.context:
                messages.append({"role": "user", "content": f"Context:\n{req.context}"})
            messages.append({"role": "user", "content": req.query})

            response, retries = await self._llm.complete(deployment, messages)
            p = response.usage.prompt_tokens
            c = response.usage.completion_tokens
            cost = calc_cost(deployment, p, c)
            result_text = response.choices[0].message.content or ""

            self._metrics.record_quality_score(
                self._agent_type, deployment, compute_quality_score(result_text),
                prompt_version=prompt_version,
            )

            # ── LLM-as-judge (샘플링, 백그라운드) ───────────────────
            if self._judge and _should_sample(self._judge_sample_rate):
                asyncio.create_task(
                    self._run_judge_safe(req.query, result_text, deployment, prompt_version)
                )

            await self._cache.set(deployment, req.query, result_text, {
                "tokens": {"prompt": p, "completion": c}, "cost_usd": cost,
            }, prompt_version=prompt_version)
            await track_user_cost(req.params, cost, p + c)
            await self._events.publish("agent.run.completed", {
                "model": deployment, "cost_usd": cost,
                "cached": False, "query_preview": req.query[:100],
                "prompt_version": prompt_version,
            })

            return AgentResponse(
                agent_type=self._agent_type, model=deployment,
                result=result_text,
                tokens={"prompt": p, "completion": c},
                cost_usd=cost, retries=retries,
                prompt_version=prompt_version,
            )

    async def _run_judge_safe(self, query: str, response: str, deployment: str, prompt_version: str = "") -> None:
        """judge 채점 — 실패해도 메인 흐름에 영향 없음."""
        try:
            scores = await self._judge.score(self._agent_type, query, response)
            if scores:
                self._metrics.record_judge_scores(
                    self._agent_type, deployment,
                    scores.relevance, scores.accuracy,
                    scores.completeness, scores.overall,
                    prompt_version=prompt_version,
                )
                logger.info(
                    "Judge scored",
                    extra={
                        "agent_type": self._agent_type,
                        "judge_overall": scores.overall,
                        "prompt_version": prompt_version,
                        **scores.as_dict(),
                    },
                )
        except Exception as e:
            logger.warning("Judge scoring failed", extra={"error": str(e)})


class OrchestratorUseCase:
    """Tool calling → 병렬 sub-agent 호출 → 결과 합성을 담당하는 유스 케이스."""

    def __init__(
        self,
        profile: dict,
        tools: list,
        llm: LLMProvider,
        metrics: MetricsRecorder,
        events: EventPublisher,
        sub_agent_invoker,
        prompt_provider: Optional[PromptProvider] = None,
        judge: Optional[JudgeScorer] = None,
        judge_sample_rate: float = 0.0,
    ):
        self._profile = profile
        self._tools = tools
        self._llm = llm
        self._metrics = metrics
        self._events = events
        self._invoker = sub_agent_invoker
        self._prompt_provider = prompt_provider
        self._judge = judge
        self._judge_sample_rate = judge_sample_rate

    async def execute(self, req: AgentRequest) -> AgentResponse:
        quota_error = await check_quota(req.params)
        if quota_error:
            self._metrics.record_quota_reject("orchestrator")
            raise QuotaExceededError(quota_error)

        self._metrics.record_agent_run("orchestrator")

        with tracer.start_as_current_span("agent-run") as span:
            span.set_attribute("agent.type", "orchestrator")
            span.set_attribute("request.query", req.query[:200])
            if req.params:
                for k, v in req.params.items():
                    span.set_attribute(f"param.{k}", str(v))

            deployment = req.model_override or self._profile["deployment"]

            # ── 프롬프트 해석 ────────────────────────────────────────
            subject_id = None
            if req.params:
                subject_id = str(req.params.get("user_id") or req.params.get("session_id") or "")
            if self._prompt_provider:
                resolution = self._prompt_provider.resolve("orchestrator", subject_id or None)
            else:
                fallback_prompt = self._profile.get("system_prompt", "")
                from domain.prompt import PromptResolution
                import hashlib
                resolution = PromptResolution(
                    system_prompt=fallback_prompt, version="fallback",
                    variant="fallback", source="fallback",
                    content_hash=hashlib.md5(fallback_prompt.encode()).hexdigest()[:8],
                )
            prompt_version = resolution.version
            span.set_attribute("prompt.version", prompt_version)
            span.set_attribute("prompt.variant", resolution.variant)
            span.set_attribute("prompt.source", resolution.source)

            messages = [{"role": "system", "content": resolution.system_prompt}]
            if req.context:
                messages.append({"role": "user", "content": f"Context:\n{req.context}"})
            messages.append({"role": "user", "content": req.query})

            # ── 1차 LLM 호출: 라우팅 결정 ───────────────────────────
            response, retries1 = await self._llm.complete(deployment, messages, tools=self._tools)
            choice = response.choices[0]

            if not choice.message.tool_calls:
                # Tool call 없이 직접 답변
                p, c = response.usage.prompt_tokens, response.usage.completion_tokens
                cost = calc_cost(deployment, p, c)
                direct_result = choice.message.content or ""
                self._metrics.record_quality_score(
                    "orchestrator", deployment, compute_quality_score(direct_result),
                    prompt_version=prompt_version,
                )
                if self._judge and _should_sample(self._judge_sample_rate):
                    asyncio.create_task(
                        self._run_judge_safe(req.query, direct_result, deployment, prompt_version)
                    )
                await track_user_cost(req.params, cost, p + c)
                await self._events.publish("agent.run.completed", {
                    "model": deployment, "cost_usd": cost,
                    "cached": False, "query_preview": req.query[:100],
                    "prompt_version": prompt_version,
                })
                return AgentResponse(
                    agent_type="orchestrator", model=deployment,
                    result=direct_result,
                    tokens={"prompt": p, "completion": c},
                    cost_usd=cost, retries=retries1,
                    prompt_version=prompt_version,
                )

            messages.append(choice.message.model_dump())

            # ── Sub-agent 병렬 호출 ──────────────────────────────────
            tool_calls = choice.message.tool_calls
            results = await asyncio.gather(
                *[self._invoker.invoke(tc, params=req.params) for tc in tool_calls]
            )
            agents_called = []
            for tc, result in zip(tool_calls, results):
                agents_called.append(tc.function.name)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            span.set_attribute("orchestrator.agents_called", ",".join(agents_called))

            # ── 2차 LLM 호출: 결과 합성 ─────────────────────────────
            final, retries2 = await self._llm.complete(deployment, messages)
            total_p = response.usage.prompt_tokens + final.usage.prompt_tokens
            total_c = response.usage.completion_tokens + final.usage.completion_tokens
            cost = calc_cost(deployment, total_p, total_c)
            final_result = final.choices[0].message.content or ""
            self._metrics.record_quality_score(
                "orchestrator", deployment, compute_quality_score(final_result),
                prompt_version=prompt_version,
            )
            if self._judge and _should_sample(self._judge_sample_rate):
                asyncio.create_task(
                    self._run_judge_safe(req.query, final_result, deployment, prompt_version)
                )
            await track_user_cost(req.params, cost, total_p + total_c)
            await self._events.publish("agent.run.completed", {
                "model": deployment, "cost_usd": cost,
                "cached": False, "query_preview": req.query[:100],
                "prompt_version": prompt_version,
            })

            return AgentResponse(
                agent_type="orchestrator", model=deployment,
                result=final_result,
                tokens={"prompt": total_p, "completion": total_c},
                cost_usd=cost, retries=retries1 + retries2,
                prompt_version=prompt_version,
            )

    async def _run_judge_safe(self, query: str, response: str, deployment: str, prompt_version: str = "") -> None:
        try:
            scores = await self._judge.score("orchestrator", query, response)
            if scores:
                self._metrics.record_judge_scores(
                    "orchestrator", deployment,
                    scores.relevance, scores.accuracy,
                    scores.completeness, scores.overall,
                    prompt_version=prompt_version,
                )
                logger.info(
                    "Judge scored",
                    extra={
                        "agent_type": "orchestrator",
                        "judge_overall": scores.overall,
                        "prompt_version": prompt_version,
                        **scores.as_dict(),
                    },
                )
        except Exception as e:
            logger.warning("Judge scoring failed", extra={"error": str(e)})
