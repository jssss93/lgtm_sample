"""Azure OpenAI LLM 프로바이더 — Circuit Breaker + 재시도 포함."""
import asyncio
import random
import time

from openai import RateLimitError, APIStatusError

from config import (
    AGENT_TYPE, MAX_RETRIES, PROMPT_LOG_MAX_LEN,
    CB_FAILURE_THRESHOLD, CB_RECOVERY_TIMEOUT, USE_LANGFUSE,
)
from domain.ports import LLMProvider
from otel_setup import (
    tracer, logger,
    llm_call_duration, token_usage_counter, cost_counter,
    request_token_histogram, rate_limit_counter, retry_counter,
    circuit_breaker_open_counter, circuit_breaker_reject_counter,
)
from stats import calc_cost, track_llm_call, track_rate_limit, track_retry


# ──────────────────────────── Circuit Breaker ───────────────────
class CircuitBreakerOpenError(RuntimeError):
    pass


class CircuitBreaker:
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(self, failure_threshold: int, recovery_timeout: float):
        self._failure_count = 0
        self._threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._state = self.CLOSED
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        return self._state

    async def can_attempt(self) -> bool:
        async with self._lock:
            if self._state == self.CLOSED:
                return True
            if self._state == self.OPEN:
                if time.time() - self._opened_at >= self._recovery_timeout:
                    self._state = self.HALF_OPEN
                    return True
                return False
            return True  # HALF_OPEN: 탐색 호출 1회 허용

    async def record_success(self):
        async with self._lock:
            self._failure_count = 0
            self._state = self.CLOSED

    async def record_failure(self) -> bool:
        """True 반환 = 방금 OPEN으로 전환됨."""
        async with self._lock:
            self._failure_count += 1
            if self._failure_count >= self._threshold:
                self._state = self.OPEN
                self._opened_at = time.time()
                return True
            return False


# ──────────────────────────── LLM Provider ──────────────────────
class AzureOpenAIProvider(LLMProvider):
    """Azure OpenAI 호출 구현. deployment별 Circuit Breaker 관리."""

    def __init__(self, endpoint: str, api_key: str, api_version: str):
        if USE_LANGFUSE:
            from langfuse.openai import AsyncAzureOpenAI
        else:
            from openai import AsyncAzureOpenAI

        self._client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )
        self._circuit_breakers: dict[str, CircuitBreaker] = {}

    def _get_cb(self, deployment: str) -> CircuitBreaker:
        if deployment not in self._circuit_breakers:
            self._circuit_breakers[deployment] = CircuitBreaker(
                CB_FAILURE_THRESHOLD, CB_RECOVERY_TIMEOUT
            )
        return self._circuit_breakers[deployment]

    async def complete(
        self,
        deployment: str,
        messages: list[dict],
        tools: list | None = None,
    ) -> tuple:
        """(response, retries_count) 반환."""
        cb = self._get_cb(deployment)

        with tracer.start_as_current_span("llm-call") as span:
            span.set_attribute("llm.model", deployment)
            span.set_attribute("llm.message_count", len(messages))
            span.set_attribute("llm.circuit_breaker.state", cb.state)

            if not await cb.can_attempt():
                circuit_breaker_reject_counter.add(1, {"llm.model": deployment, "agent.type": AGENT_TYPE})
                span.set_attribute("llm.circuit_breaker.rejected", True)
                logger.warning("Circuit breaker OPEN — call rejected", extra={"model": deployment})
                raise CircuitBreakerOpenError(
                    f"Circuit breaker OPEN for {deployment}. "
                    f"Retry after {CB_RECOVERY_TIMEOUT:.0f}s."
                )

            user_msgs = [
                m["content"] for m in messages
                if m.get("role") == "user" and isinstance(m.get("content"), str)
            ]
            span.set_attribute("llm.prompt", " | ".join(user_msgs)[:PROMPT_LOG_MAX_LEN])

            retries = 0
            start = time.time()

            for attempt in range(MAX_RETRIES + 1):
                try:
                    kwargs: dict = {"model": deployment, "messages": messages, "temperature": 0.7}
                    if tools:
                        kwargs["tools"] = tools
                    response = await self._client.chat.completions.create(**kwargs)
                    break
                except RateLimitError as e:
                    retries += 1
                    rate_limit_counter.add(1, {"llm.model": deployment, "agent.type": AGENT_TYPE})
                    retry_counter.add(1, {"llm.model": deployment, "reason": "rate_limit"})
                    await track_rate_limit()

                    retry_after = float(e.response.headers.get("retry-after", 2 ** attempt))
                    retry_after += random.uniform(0, retry_after * 0.5)
                    span.add_event("rate_limit_hit", {"attempt": attempt + 1, "retry_after": retry_after})
                    logger.warning(
                        f"Rate limit 429 — retry {attempt + 1}/{MAX_RETRIES}, wait {retry_after:.1f}s",
                        extra={"model": deployment, "retry_after": retry_after},
                    )
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(retry_after)
                    else:
                        if await cb.record_failure():
                            circuit_breaker_open_counter.add(1, {"llm.model": deployment, "agent.type": AGENT_TYPE})
                            span.set_attribute("llm.circuit_breaker.opened", True)
                            logger.warning("Circuit breaker opened", extra={"model": deployment})
                        raise
                except APIStatusError as e:
                    retries += 1
                    retry_counter.add(1, {"llm.model": deployment, "reason": f"status_{e.status_code}"})
                    await track_retry()
                    span.add_event("api_error", {"attempt": attempt + 1, "status_code": e.status_code})
                    logger.warning(
                        f"API error {e.status_code} — retry {attempt + 1}/{MAX_RETRIES}",
                        extra={"model": deployment, "status_code": e.status_code},
                    )
                    if attempt < MAX_RETRIES:
                        backoff = 2 ** attempt + random.uniform(0, 1)
                        await asyncio.sleep(backoff)
                    else:
                        if await cb.record_failure():
                            circuit_breaker_open_counter.add(1, {"llm.model": deployment, "agent.type": AGENT_TYPE})
                            span.set_attribute("llm.circuit_breaker.opened", True)
                            logger.warning("Circuit breaker opened", extra={"model": deployment})
                        raise

            await cb.record_success()
            duration = time.time() - start
            usage = response.usage
            call_cost = calc_cost(deployment, usage.prompt_tokens, usage.completion_tokens)

            span.set_attribute("llm.prompt_tokens", usage.prompt_tokens)
            span.set_attribute("llm.completion_tokens", usage.completion_tokens)
            span.set_attribute("llm.total_tokens", usage.total_tokens)
            span.set_attribute("llm.duration", round(duration, 3))
            span.set_attribute("llm.cost_usd", call_cost)
            span.set_attribute("llm.retries", retries)

            resp_content = response.choices[0].message.content or ""
            span.set_attribute("llm.response", resp_content[:PROMPT_LOG_MAX_LEN])
            if response.choices[0].message.tool_calls:
                tc_names = [tc.function.name for tc in response.choices[0].message.tool_calls]
                span.set_attribute("llm.tool_calls", ",".join(tc_names))

            llm_call_duration.record(duration, {"llm.model": deployment, "agent.type": AGENT_TYPE})
            token_usage_counter.add(usage.prompt_tokens, {"llm.model": deployment, "type": "prompt"})
            token_usage_counter.add(usage.completion_tokens, {"llm.model": deployment, "type": "completion"})
            request_token_histogram.record(usage.total_tokens, {"llm.model": deployment, "agent.type": AGENT_TYPE})
            cost_counter.add(call_cost, {"llm.model": deployment, "agent.type": AGENT_TYPE})

            await track_llm_call(deployment, usage.prompt_tokens, usage.completion_tokens, call_cost, retries)

            logger.info("LLM call completed", extra={
                "model": deployment,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "duration": round(duration, 3),
                "cost_usd": round(call_cost, 6),
                "retries": retries,
            })
            return response, retries
