import asyncio
import json
import os
import random
import time

import httpx
from openai import AsyncAzureOpenAI, RateLimitError, APIStatusError

from config import (
    AGENT_TYPE, MAX_RETRIES, PROMPT_LOG_MAX_LEN, SUB_AGENT_URLS,
)
from otel_setup import (
    tracer, logger,
    llm_call_duration, token_usage_counter, cost_counter,
    request_token_histogram, rate_limit_counter, retry_counter,
)
from stats import calc_cost, track_llm_call, track_rate_limit, track_retry

# ──────────────────────────── AOAI Client ───────────────────────
aoai = AsyncAzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
    api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
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
    with tracer.start_as_current_span("llm-call") as span:
        span.set_attribute("llm.model", deployment)
        span.set_attribute("llm.message_count", len(messages))

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
                    raise

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
    url = SUB_AGENT_URLS.get(fn_name)

    if not url:
        return f"Error: unknown tool '{fn_name}'"

    with tracer.start_as_current_span("sub-agent-call") as span:
        span.set_attribute("sub_agent.name", fn_name)
        span.set_attribute("sub_agent.url", url)

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
            logger.info("Sub-agent call completed", extra={"tool": fn_name, "status": "success"})
            return result
        except Exception as e:
            span.set_attribute("sub_agent.status", "error")
            span.set_attribute("error", True)
            logger.error("Sub-agent call failed", extra={"tool": fn_name, "error": str(e)})
            return f"Error calling {fn_name}: {e}"
