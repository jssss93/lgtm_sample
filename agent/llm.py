import asyncio
import json
import os
import random
import time

import httpx
from openai import RateLimitError, APIStatusError
from config import (
    AGENT_TYPE, MAX_RETRIES, PROMPT_LOG_MAX_LEN, SUB_AGENT_URLS,
    USE_DAPR, USE_LANGFUSE, DAPR_HTTP_PORT, DAPR_APP_ID_MAP, DAPR_SECRET_STORE,
    CB_FAILURE_THRESHOLD, CB_RECOVERY_TIMEOUT,
)

if USE_LANGFUSE:
    from langfuse.openai import AsyncAzureOpenAI
else:
    from openai import AsyncAzureOpenAI


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
            prev = self._state
            self._failure_count = 0
            self._state = self.CLOSED
            return prev  # 상태 전환 여부 반환용

    async def record_failure(self) -> bool:
        """True 반환 = 방금 OPEN으로 전환됨"""
        async with self._lock:
            self._failure_count += 1
            if self._failure_count >= self._threshold:
                self._state = self.OPEN
                self._opened_at = time.time()
                return True
            return False


# deployment별 circuit breaker 인스턴스
_circuit_breakers: dict[str, CircuitBreaker] = {}


def _get_circuit_breaker(deployment: str) -> CircuitBreaker:
    if deployment not in _circuit_breakers:
        _circuit_breakers[deployment] = CircuitBreaker(CB_FAILURE_THRESHOLD, CB_RECOVERY_TIMEOUT)
    return _circuit_breakers[deployment]
from otel_setup import (
    tracer, logger,
    llm_call_duration, token_usage_counter, cost_counter,
    request_token_histogram, rate_limit_counter, retry_counter,
    circuit_breaker_open_counter, circuit_breaker_reject_counter,
)
from stats import calc_cost, track_llm_call, track_rate_limit, track_retry


# ──────────────────────────── Dapr Secret Store ────────────────
def _fetch_dapr_secret(key: str) -> dict:
    """시작 시 Dapr Secret Store에서 시크릿을 동기적으로 가져온다."""
    import urllib.request
    url = f"http://localhost:{DAPR_HTTP_PORT}/v1.0/secrets/{DAPR_SECRET_STORE}/{key}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


def _get_aoai_credentials() -> tuple[str, str, str]:
    """AOAI 자격증명을 Dapr Secret Store 또는 환경변수에서 가져온다."""
    if USE_DAPR:
        secrets = _fetch_dapr_secret("azure-openai")
        if secrets:
            logger.info("AOAI credentials loaded from Dapr Secret Store")
            return (
                secrets.get("endpoint", ""),
                secrets.get("api-key", ""),
                secrets.get("api-version", "2024-12-01-preview"),
            )
    return (
        os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        os.getenv("AZURE_OPENAI_API_KEY", ""),
        os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
    )


# ──────────────────────────── AOAI Client ───────────────────────
_endpoint, _api_key, _api_version = _get_aoai_credentials()
aoai = AsyncAzureOpenAI(
    azure_endpoint=_endpoint,
    api_key=_api_key,
    api_version=_api_version,
)

# ──────────────────────────── Shared httpx client ───────────────
_http_client: httpx.AsyncClient | None = None


async def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=60.0)
    return _http_client


async def close_http_client():
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


# ──────────────────────────── LLM Call ──────────────────────────
async def call_aoai(deployment: str, messages: list[dict], tools: list | None = None) -> tuple:
    """Returns (response, retries_count)."""
    cb = _get_circuit_breaker(deployment)

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
        prompt_text = " | ".join(user_msgs)[:PROMPT_LOG_MAX_LEN]
        span.set_attribute("llm.prompt", prompt_text)

        retries = 0
        start = time.time()

        for attempt in range(MAX_RETRIES + 1):
            try:
                kwargs = {"model": deployment, "messages": messages, "temperature": 0.7}
                if tools:
                    kwargs["tools"] = tools
                response = await aoai.chat.completions.create(**kwargs)
                break
            except RateLimitError as e:
                retries += 1
                rate_limit_counter.add(1, {"llm.model": deployment, "agent.type": AGENT_TYPE})
                retry_counter.add(1, {"llm.model": deployment, "reason": "rate_limit"})
                await track_rate_limit()

                retry_after = float(e.response.headers.get("retry-after", 2 ** attempt))
                # jitter 추가로 thundering herd 방지
                retry_after += random.uniform(0, retry_after * 0.5)
                span.add_event("rate_limit_hit", {"attempt": attempt + 1, "retry_after": retry_after})
                logger.warning(
                    f"Rate limit 429 — retry {attempt + 1}/{MAX_RETRIES}, wait {retry_after:.1f}s",
                    extra={"model": deployment, "retry_after": retry_after},
                )

                if attempt < MAX_RETRIES:
                    await asyncio.sleep(retry_after)
                else:
                    just_opened = await cb.record_failure()
                    if just_opened:
                        circuit_breaker_open_counter.add(1, {"llm.model": deployment, "agent.type": AGENT_TYPE})
                        span.set_attribute("llm.circuit_breaker.opened", True)
                        logger.warning("Circuit breaker opened", extra={"model": deployment, "failure_count": CB_FAILURE_THRESHOLD})
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
                    # jitter가 포함된 exponential backoff
                    backoff = 2 ** attempt + random.uniform(0, 1)
                    await asyncio.sleep(backoff)
                else:
                    just_opened = await cb.record_failure()
                    if just_opened:
                        circuit_breaker_open_counter.add(1, {"llm.model": deployment, "agent.type": AGENT_TYPE})
                        span.set_attribute("llm.circuit_breaker.opened", True)
                        logger.warning("Circuit breaker opened", extra={"model": deployment, "failure_count": CB_FAILURE_THRESHOLD})
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


async def execute_tool_call(tool_call, params: dict | None = None) -> str:
    fn_name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)

    # Dapr Service Invocation vs 직접 HTTP 호출
    if USE_DAPR:
        dapr_app_id = DAPR_APP_ID_MAP.get(fn_name)
        if not dapr_app_id:
            return f"Error: unknown tool '{fn_name}'"
        url = f"http://localhost:{DAPR_HTTP_PORT}/v1.0/invoke/{dapr_app_id}/method"
    else:
        url = SUB_AGENT_URLS.get(fn_name)
        if not url:
            return f"Error: unknown tool '{fn_name}'"

    with tracer.start_as_current_span("sub-agent-call") as span:
        span.set_attribute("sub_agent.name", fn_name)
        span.set_attribute("sub_agent.url", url)
        span.set_attribute("sub_agent.via_dapr", USE_DAPR)

        try:
            client = await get_http_client()
            query = args.get("query") or args.get("text", "")
            body: dict = {"query": query}
            if params:
                body["params"] = params
            resp = await client.post(f"{url}/run", json=body)
            resp.raise_for_status()
            result = resp.json()["result"]
            span.set_attribute("sub_agent.status", "success")
            logger.info("Sub-agent call completed", extra={
                "tool": fn_name, "status": "success", "via_dapr": USE_DAPR,
            })
            return result
        except Exception as e:
            span.set_attribute("sub_agent.status", "error")
            span.set_attribute("error", True)
            logger.error("Sub-agent call failed", extra={"tool": fn_name, "error": str(e)})
            return f"Error calling {fn_name}: {e}"
