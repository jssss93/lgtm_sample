import asyncio
import hashlib
import json
import time
from collections import OrderedDict

import httpx

from config import CACHE_TTL, CACHE_MAX_SIZE, USE_DAPR, DAPR_HTTP_PORT, DAPR_STATE_STORE

_cache_lock = asyncio.Lock()
_cache: OrderedDict[str, tuple[str, float, dict]] = OrderedDict()


def _cache_key(deployment: str, query: str) -> str:
    normalized = query.strip().lower()
    return hashlib.sha256(f"{deployment}:{normalized}".encode()).hexdigest()


# ──────────────────────────── Dapr State Store 구현 ─────────────
async def _dapr_state_get(key: str) -> tuple[str, dict] | None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"http://localhost:{DAPR_HTTP_PORT}/v1.0/state/{DAPR_STATE_STORE}/{key}"
        )
        if resp.status_code == 200 and resp.text:
            data = resp.json()
            ts = data.get("ts", 0)
            if time.time() - ts < CACHE_TTL:
                return data["result"], data.get("meta", {})
            # TTL 만료 → 삭제
            await client.delete(
                f"http://localhost:{DAPR_HTTP_PORT}/v1.0/state/{DAPR_STATE_STORE}/{key}"
            )
    return None


async def _dapr_state_set(key: str, result: str, meta: dict):
    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.post(
            f"http://localhost:{DAPR_HTTP_PORT}/v1.0/state/{DAPR_STATE_STORE}",
            json=[{"key": key, "value": {"result": result, "meta": meta, "ts": time.time()}}],
        )


# ──────────────────────────── 통합 인터페이스 ──────────────────
async def cache_get(deployment: str, query: str) -> tuple[str, dict] | None:
    key = _cache_key(deployment, query)

    if USE_DAPR:
        try:
            return await _dapr_state_get(key)
        except Exception:
            return None

    async with _cache_lock:
        if key in _cache:
            result, ts, meta = _cache[key]
            if time.time() - ts < CACHE_TTL:
                _cache.move_to_end(key)
                return result, meta
            else:
                del _cache[key]
    return None


async def cache_set(deployment: str, query: str, result: str, meta: dict):
    key = _cache_key(deployment, query)

    if USE_DAPR:
        try:
            await _dapr_state_set(key, result, meta)
        except Exception:
            pass
        return

    async with _cache_lock:
        _cache[key] = (result, time.time(), meta)
        if len(_cache) > CACHE_MAX_SIZE:
            _cache.popitem(last=False)


async def cache_clear() -> int:
    if USE_DAPR:
        return 0  # Dapr state store는 개별 삭제만 지원
    async with _cache_lock:
        count = len(_cache)
        _cache.clear()
        return count


async def cache_size() -> int:
    if USE_DAPR:
        return -1  # Dapr state store는 크기 조회 미지원
    async with _cache_lock:
        return len(_cache)
