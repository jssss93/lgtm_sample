# agent/ — 멀티에이전트 앱 코드

4개 에이전트(orchestrator, search, summarizer, coder)가 동일한 코드베이스를 공유하며, `AGENT_TYPE` 환경변수로 역할이 결정된다.

## 아키텍처 — Hexagonal (Ports & Adapters)

```
domain/          ← 순수 비즈니스 정책. 외부 의존 없음
  ports.py       ← CacheBackend, LLMProvider, MetricsRecorder, EventPublisher ABC
  value_objects.py ← LLMTokens, CachedResult, UserQuota (불변 값 객체)

application/     ← 유스 케이스. 도메인 포트만 의존
  use_cases.py   ← SubAgentUseCase, OrchestratorUseCase

infrastructure/  ← 포트 구현체. 외부 서비스와 실제 연결
  cache_memory.py   ← MemoryCacheBackend (LRU + TTL)
  cache_dapr.py     ← DaprCacheBackend (Redis via Dapr)
  llm_aoai.py       ← AzureOpenAIProvider (Circuit Breaker + retry 포함)
  metrics_otel.py   ← OTelMetricsRecorder, NoOpMetricsRecorder
  events.py         ← DaprEventPublisher, NoOpEventPublisher
  sub_agent_invoker.py ← SubAgentInvoker (HTTP / Dapr Service Invocation)

container.py     ← DI 조립. 앱 시작 시 build_use_case() 한 번 호출
app.py           ← FastAPI thin layer. HTTP ↔ UseCase 변환만 담당

# 하위호환 퍼사드 (기존 코드와의 호환성 유지)
cache.py         ← cache_get/set/clear/size 위임
llm.py           ← call_aoai/execute_tool_call 위임

# 변경 없음
config.py        ← 환경변수, 프로필, 가격표, Dapr 설정
models.py        ← AgentRequest, AgentResponse Pydantic 스키마
stats.py         ← 인메모리 비용/쿼터 집계
otel_setup.py    ← OTel SDK 초기화 (Tracer, Meter, Logger)
```

## 핵심 설계 원칙

- **DIP 준수**: `app.py`는 포트(ABC)만 알고 구현체를 모름
- **SRP 준수**: 캐시는 `MemoryCacheBackend`, LLM은 `AzureOpenAIProvider`, 메트릭은 `OTelMetricsRecorder` — 각자 단일 책임
- **테스트**: `NoOpMetricsRecorder`, `NoOpEventPublisher`로 사이드이펙트 없는 단위 테스트 가능
- **확장**: LLM 교체 → `LLMProvider` 구현 추가. 캐시 교체 → `CacheBackend` 구현 추가

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
POST /run (app.py)
  └─ use_case.execute(req)          ← container.build_use_case()로 주입
       ├─ check_quota()
       ├─ [SubAgent] cache.get() → [miss] → llm.complete() → cache.set()
       └─ [Orchestrator] llm.complete(tools) → gather(invoker.invoke × N) → llm.complete()
```
