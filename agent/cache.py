"""
캐시 퍼사드 — 하위호환 모듈 수준 API를 유지하면서
내부는 CacheBackend 구현체에 위임한다.

직접 사용 대신 container.py를 통해 주입된 CacheBackend를 사용하길 권장한다.
"""
from config import CACHE_TTL, CACHE_MAX_SIZE, USE_DAPR, DAPR_HTTP_PORT, DAPR_STATE_STORE
from infrastructure.cache_memory import MemoryCacheBackend, _cache_key  # noqa: F401 (테스트에서 직접 임포트)
from infrastructure.cache_dapr import DaprCacheBackend

# 모듈 로드 시점에 백엔드 선택
_backend = (
    DaprCacheBackend(DAPR_HTTP_PORT, DAPR_STATE_STORE, CACHE_TTL)
    if USE_DAPR
    else MemoryCacheBackend(CACHE_TTL, CACHE_MAX_SIZE)
)


async def cache_get(deployment: str, query: str):
    return await _backend.get(deployment, query)


async def cache_set(deployment: str, query: str, result: str, meta: dict):
    await _backend.set(deployment, query, result, meta)


async def cache_clear() -> int:
    return await _backend.clear()


async def cache_size() -> int:
    return await _backend.size()
