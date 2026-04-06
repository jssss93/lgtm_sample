"""애플리케이션 유스 케이스 — 비즈니스 로직만, 인프라 직접 의존 없음."""
import asyncio

from domain.ports import CacheBackend, LLMProvider, MetricsRecorder, EventPublisher
from models import AgentRequest, AgentResponse
from otel_setup import tracer, logger
from stats import calc_cost, track_user_cost, track_cache_hit, track_cache_miss, check_quota


class QuotaExceededError(Exception):
    pass


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
    ):
        self._agent_type = agent_type
        self._profile = profile
        self._cache = cache
        self._llm = llm
        self._metrics = metrics
        self._events = events

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

            # ── 캐시 조회 ──────────────────────────────────────────
            cached = await self._cache.get(deployment, req.query)
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
                )

            # ── 캐시 미스 → LLM 호출 ────────────────────────────────
            self._metrics.record_cache_miss(self._agent_type)
            await track_cache_miss()
            span.set_attribute("cache.hit", False)

            messages = [{"role": "system", "content": self._profile["system_prompt"]}]
            if req.context:
                messages.append({"role": "user", "content": f"Context:\n{req.context}"})
            messages.append({"role": "user", "content": req.query})

            response, retries = await self._llm.complete(deployment, messages)
            p = response.usage.prompt_tokens
            c = response.usage.completion_tokens
            cost = calc_cost(deployment, p, c)
            result_text = response.choices[0].message.content or ""

            await self._cache.set(deployment, req.query, result_text, {
                "tokens": {"prompt": p, "completion": c}, "cost_usd": cost,
            })
            await track_user_cost(req.params, cost, p + c)
            await self._events.publish("agent.run.completed", {
                "model": deployment, "cost_usd": cost,
                "cached": False, "query_preview": req.query[:100],
            })

            return AgentResponse(
                agent_type=self._agent_type, model=deployment,
                result=result_text,
                tokens={"prompt": p, "completion": c},
                cost_usd=cost, retries=retries,
            )


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
    ):
        self._profile = profile
        self._tools = tools
        self._llm = llm
        self._metrics = metrics
        self._events = events
        self._invoker = sub_agent_invoker

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
            messages = [{"role": "system", "content": self._profile["system_prompt"]}]
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
                await track_user_cost(req.params, cost, p + c)
                await self._events.publish("agent.run.completed", {
                    "model": deployment, "cost_usd": cost,
                    "cached": False, "query_preview": req.query[:100],
                })
                return AgentResponse(
                    agent_type="orchestrator", model=deployment,
                    result=choice.message.content or "",
                    tokens={"prompt": p, "completion": c},
                    cost_usd=cost, retries=retries1,
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
            await track_user_cost(req.params, cost, total_p + total_c)
            await self._events.publish("agent.run.completed", {
                "model": deployment, "cost_usd": cost,
                "cached": False, "query_preview": req.query[:100],
            })

            return AgentResponse(
                agent_type="orchestrator", model=deployment,
                result=final.choices[0].message.content or "",
                tokens={"prompt": total_p, "completion": total_c},
                cost_usd=cost, retries=retries1 + retries2,
            )
