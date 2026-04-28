import asyncio
import time

from config import PRICING, USER_TOKEN_QUOTA, USER_COST_QUOTA

_stats_lock = asyncio.Lock()
_stats = {
    "total_requests": 0,
    "total_prompt_tokens": 0,
    "total_completion_tokens": 0,
    "total_cost_usd": 0.0,
    "total_retries": 0,
    "total_rate_limits": 0,
    "total_cache_hits": 0,
    "total_cache_misses": 0,
    "by_model": {},
    "by_user": {},
    "started_at": time.time(),
}


def calc_cost(deployment: str, prompt_tokens: int, completion_tokens: int) -> float:
    p = PRICING.get(deployment, {"prompt": 0, "completion": 0})
    return round(
        prompt_tokens * p["prompt"] / 1_000_000
        + completion_tokens * p["completion"] / 1_000_000,
        6,
    )


async def track_llm_call(deployment: str, prompt_tokens: int, completion_tokens: int, cost: float, retries: int = 0):
    async with _stats_lock:
        _stats["total_requests"] += 1
        _stats["total_prompt_tokens"] += prompt_tokens
        _stats["total_completion_tokens"] += completion_tokens
        _stats["total_cost_usd"] += cost
        _stats["total_retries"] += retries
        if deployment not in _stats["by_model"]:
            _stats["by_model"][deployment] = {
                "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "calls": 0,
            }
        m = _stats["by_model"][deployment]
        m["prompt_tokens"] += prompt_tokens
        m["completion_tokens"] += completion_tokens
        m["cost_usd"] += cost
        m["calls"] += 1


async def track_rate_limit():
    async with _stats_lock:
        _stats["total_rate_limits"] += 1
        _stats["total_retries"] += 1


async def track_retry():
    async with _stats_lock:
        _stats["total_retries"] += 1


async def track_cache_hit():
    async with _stats_lock:
        _stats["total_cache_hits"] += 1


async def track_cache_miss():
    async with _stats_lock:
        _stats["total_cache_misses"] += 1


async def track_user_cost(params: dict | None, cost: float, tokens: int):
    if not params:
        return
    user_id = str(params.get("user_id", ""))
    session_id = str(params.get("session_id", ""))
    if not user_id and not session_id:
        return
    async with _stats_lock:
        key = user_id or session_id
        if key not in _stats["by_user"]:
            _stats["by_user"][key] = {
                "user_id": user_id, "session_id": session_id,
                "cost_usd": 0.0, "tokens": 0, "requests": 0,
            }
        u = _stats["by_user"][key]
        u["cost_usd"] += cost
        u["tokens"] += tokens
        u["requests"] += 1


async def check_quota(params: dict | None) -> str | None:
    """사용자 quota 확인. 초과 시 에러 메시지 반환, 통과 시 None."""
    if not params:
        return None
    if USER_TOKEN_QUOTA == 0 and USER_COST_QUOTA == 0:
        return None

    user_id = str(params.get("user_id", ""))
    session_id = str(params.get("session_id", ""))
    key = user_id or session_id
    if not key:
        return None

    async with _stats_lock:
        user_data = _stats["by_user"].get(key)
        if not user_data:
            return None
        if USER_TOKEN_QUOTA > 0 and user_data["tokens"] >= USER_TOKEN_QUOTA:
            return f"Token quota exceeded ({user_data['tokens']}/{USER_TOKEN_QUOTA})"
        if USER_COST_QUOTA > 0 and user_data["cost_usd"] >= USER_COST_QUOTA:
            return f"Cost quota exceeded (${user_data['cost_usd']:.4f}/${USER_COST_QUOTA})"
    return None


async def get_stats(cache_size: int, cache_ttl: int) -> dict:
    async with _stats_lock:
        uptime = time.time() - _stats["started_at"]
        return {
            "uptime_seconds": round(uptime, 1),
            "total_requests": _stats["total_requests"],
            "total_tokens": {
                "prompt": _stats["total_prompt_tokens"],
                "completion": _stats["total_completion_tokens"],
                "total": _stats["total_prompt_tokens"] + _stats["total_completion_tokens"],
            },
            "total_cost_usd": round(_stats["total_cost_usd"], 6),
            "total_retries": _stats["total_retries"],
            "total_rate_limits": _stats["total_rate_limits"],
            "cache": {
                "hits": _stats["total_cache_hits"],
                "misses": _stats["total_cache_misses"],
                "hit_rate": round(
                    _stats["total_cache_hits"]
                    / max(_stats["total_cache_hits"] + _stats["total_cache_misses"], 1)
                    * 100, 1,
                ),
                "size": cache_size,
                "ttl_seconds": cache_ttl,
            },
            "by_model": {
                model: {
                    **data,
                    "cost_usd": round(data["cost_usd"], 6),
                    "avg_tokens_per_call": round(
                        (data["prompt_tokens"] + data["completion_tokens"]) / max(data["calls"], 1), 1,
                    ),
                }
                for model, data in _stats["by_model"].items()
            },
            "by_user": dict(_stats["by_user"]),
            "pricing_per_1m_tokens": PRICING,
        }
