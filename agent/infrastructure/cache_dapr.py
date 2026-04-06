"""Dapr State Store 캐시 백엔드."""
import hashlib
import time
from typing import Optional, Tuple

import httpx

from domain.ports import CacheBackend


def _cache_key(deployment: str, query: str) -> str:
    normalized = query.strip().lower()
    return hashlib.sha256(f"{deployment}:{normalized}".encode()).hexdigest()


class DaprCacheBackend(CacheBackend):
    def __init__(self, dapr_port: int, state_store: str, ttl_seconds: int):
        self._port = dapr_port
        self._state_store = state_store
        self._ttl = ttl_seconds

    def _state_url(self, key: str = "") -> str:
        base = f"http://localhost:{self._port}/v1.0/state/{self._state_store}"
        return f"{base}/{key}" if key else base

    async def get(self, deployment: str, query: str) -> Optional[Tuple[str, dict]]:
        key = _cache_key(deployment, query)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(self._state_url(key))
                if resp.status_code == 200 and resp.text:
                    data = resp.json()
                    ts = data.get("ts", 0)
                    if time.time() - ts < self._ttl:
                        return data["result"], data.get("meta", {})
                    # TTL 만료 → 삭제
                    await client.delete(self._state_url(key))
        except Exception:
            pass
        return None

    async def set(self, deployment: str, query: str, result: str, meta: dict) -> None:
        key = _cache_key(deployment, query)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    self._state_url(),
                    json=[{"key": key, "value": {"result": result, "meta": meta, "ts": time.time()}}],
                )
        except Exception:
            pass

    async def clear(self) -> int:
        return 0  # Dapr State Store는 개별 삭제만 지원

    async def size(self) -> int:
        return -1  # Dapr State Store는 크기 조회 미지원
