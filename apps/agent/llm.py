"""
LLM 퍼사드 — 하위호환 모듈 수준 API를 유지하면서
내부는 AzureOpenAIProvider / SubAgentInvoker에 위임한다.

직접 사용 대신 container.py를 통해 주입된 LLMProvider를 사용하길 권장한다.
"""
import json
import os

from config import USE_DAPR, DAPR_HTTP_PORT, DAPR_SECRET_STORE, USE_LANGFUSE
from infrastructure.llm_aoai import AzureOpenAIProvider, CircuitBreakerOpenError  # noqa: F401
from infrastructure.sub_agent_invoker import SubAgentInvoker, get_http_client, close_http_client  # noqa: F401


# ──────────────────────────── 자격증명 로드 ─────────────────────
def _fetch_dapr_secret(key: str) -> dict:
    import urllib.request
    url = f"http://localhost:{DAPR_HTTP_PORT}/v1.0/secrets/{DAPR_SECRET_STORE}/{key}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


def _get_aoai_credentials() -> tuple[str, str, str]:
    if USE_DAPR:
        secrets = _fetch_dapr_secret("azure-openai")
        if secrets:
            from otel_setup import logger
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


# ──────────────────────────── 모듈 수준 싱글턴 ──────────────────
_endpoint, _api_key, _api_version = _get_aoai_credentials()
_provider = AzureOpenAIProvider(_endpoint, _api_key, _api_version)
_invoker = SubAgentInvoker()


# ──────────────────────────── 하위호환 API ──────────────────────
async def call_aoai(deployment: str, messages: list[dict], tools: list | None = None) -> tuple:
    return await _provider.complete(deployment, messages, tools)


async def execute_tool_call(tool_call, params: dict | None = None) -> str:
    return await _invoker.invoke(tool_call, params)
