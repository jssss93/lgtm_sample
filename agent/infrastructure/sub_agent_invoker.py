"""Sub-agent HTTP 호출 — Dapr Service Invocation 또는 직접 HTTP."""
import json

import httpx

from config import USE_DAPR, DAPR_HTTP_PORT, DAPR_APP_ID_MAP, SUB_AGENT_URLS
from otel_setup import tracer, logger

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


class SubAgentInvoker:
    """Tool call을 받아 해당 sub-agent를 HTTP로 호출하고 결과를 반환한다."""

    async def invoke(self, tool_call, params: dict | None = None) -> str:
        fn_name = tool_call.function.name
        args = json.loads(tool_call.function.arguments)

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
