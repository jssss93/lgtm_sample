# agent/ — 멀티에이전트 앱 코드

4개 에이전트(orchestrator, search, summarizer, coder)가 동일한 코드베이스를 공유하며, `AGENT_TYPE` 환경변수로 역할이 결정된다.

## 파일 구조

| 파일 | 역할 |
|------|------|
| `app.py` | FastAPI 엔드포인트 (`/run`, `/health`, `/stats`, `/cache/clear`, `/events`). orchestrator는 `_run_orchestrator()` (tool calling → 병렬 sub-agent 호출 → 결과 합성), sub-agent는 `_run_sub_agent()` (캐시 확인 → LLM 호출 → 캐시 저장) |
| `config.py` | 환경변수 로드, 에이전트 프로필(system_prompt + deployment), 가격표(`PRICING`), Dapr 설정(`USE_DAPR`, `DAPR_HTTP_PORT`), sub-agent URL 매핑, orchestrator tool 정의(`ORCHESTRATOR_TOOLS`) |
| `llm.py` | Azure OpenAI 호출 (`call_aoai`): 재시도(exponential backoff + jitter), 429 rate limit 처리. sub-agent 호출 (`execute_tool_call`): HTTP 직접 또는 Dapr Service Invocation. AOAI 인증: Dapr Secret Store 또는 환경변수 |
| `cache.py` | 듀얼 모드 캐시. 인메모리: OrderedDict + LRU 방출 + TTL. Dapr: State Store (Redis). 캐시 키: `SHA256(deployment + normalized_query)` |
| `models.py` | Pydantic 스키마. `AgentRequest`(query, context, params, model_override), `AgentResponse`(agent_type, model, result, tokens, cost_usd, cached, retries) |
| `stats.py` | 비용 추적(`calc_cost`), 사용량 집계(`track_llm_call`), 쿼터 체크(`check_quota` — 일일 토큰/비용 제한), 통계 반환(`get_stats`) |
| `otel_setup.py` | OpenTelemetry SDK 초기화. Tracer + Meter + Logger 생성. 커스텀 메트릭: `agent_run_counter`, `agent_error_counter`, `llm_call_duration`, `token_usage_counter`, `cost_counter`, `cache_hit/miss_counter`, `quota_reject_counter`. FastAPI + httpx 자동 계측 |
| `Dockerfile` | `python:3.12-slim`, non-root user(`app`), port 8000 |

## 주요 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `AGENT_TYPE` | `default` | orchestrator / search / summarizer / coder |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTel Collector gRPC |
| `OTEL_SERVICE_NAME` | `ai-agent` | 서비스 식별명 |
| `USE_DAPR` | `false` | Dapr 기능 활성화 |
| `CACHE_TTL_SECONDS` | `300` | 캐시 TTL |
| `CACHE_MAX_SIZE` | `100` | 인메모리 캐시 최대 항목 수 |
| `USER_TOKEN_QUOTA` | `0` | 일일 토큰 제한 (0=무제한) |
| `USER_COST_QUOTA` | `0` | 일일 비용 제한 USD (0=무제한) |

## 데이터 흐름

```
Orchestrator:
  POST /run → quota check → call_aoai (routing) → asyncio.gather(execute_tool_call × N) → call_aoai (합성) → response

Sub-Agent:
  POST /run → quota check → cache_get → [miss] → call_aoai → cache_set → response
                           → [hit]  → return cached → response (cost=0)
```
