import asyncio
import hashlib
import time
from collections import OrderedDict

from config import CACHE_TTL, CACHE_MAX_SIZE

_cache_lock = asyncio.Lock()
_cache: OrderedDict[str, tuple[str, float, dict]] = OrderedDict()


def _cache_key(deployment: str, query: str) -> str:
    normalized = query.strip().lower()
    return hashlib.sha256(f"{deployment}:{normalized}".encode()).hexdigest()


async def cache_get(deployment: str, query: str) -> tuple[str, dict] | None:
    key = _cache_key(deployment, query)
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
    async with _cache_lock:
        _cache[key] = (result, time.time(), meta)
        if len(_cache) > CACHE_MAX_SIZE:
            _cache.popitem(last=False)


async def cache_clear() -> int:
    async with _cache_lock:
        count = len(_cache)
        _cache.clear()
        return count


async def cache_size() -> int:
    async with _cache_lock:
        return len(_cache)
