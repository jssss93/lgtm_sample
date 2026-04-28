"""인메모리 LRU 캐시 백엔드."""
import asyncio
import hashlib
import time
from collections import OrderedDict
from typing import Optional, Tuple

from domain.ports import CacheBackend


def _cache_key(deployment: str, query: str, prompt_version: str = "") -> str:
    normalized = query.strip().lower()
    raw = f"{deployment}:{prompt_version}:{normalized}" if prompt_version else f"{deployment}:{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()


class MemoryCacheBackend(CacheBackend):
    def __init__(self, ttl_seconds: int, max_size: int):
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._cache: OrderedDict[str, Tuple[str, float, dict]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, deployment: str, query: str, prompt_version: str = "") -> Optional[Tuple[str, dict]]:
        key = _cache_key(deployment, query, prompt_version)
        async with self._lock:
            if key in self._cache:
                result, ts, meta = self._cache[key]
                if time.time() - ts < self._ttl:
                    self._cache.move_to_end(key)
                    return result, meta
                else:
                    del self._cache[key]
        return None

    async def set(self, deployment: str, query: str, result: str, meta: dict, prompt_version: str = "") -> None:
        key = _cache_key(deployment, query, prompt_version)
        async with self._lock:
            self._cache[key] = (result, time.time(), meta)
            if len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    async def clear(self) -> int:
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    async def size(self) -> int:
        async with self._lock:
            return len(self._cache)
