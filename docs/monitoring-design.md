# Multi-Agent AI 시스템 모니터링 설계 문서

## 1. 개요

### 1.1 목적
멀티에이전트 AI 시스템의 성능, 비용, 안정성을 실시간으로 관측하고,
이상 상황을 조기에 감지하기 위한 Observability 스택 설계 문서.

### 1.2 대상 시스템
Azure OpenAI 기반 멀티에이전트 시스템으로, Orchestrator가 사용자 요청을 분석하여
전문 Sub-Agent(Search, Summarizer, Coder)에게 작업을 분배하는 구조.

### 1.3 기술 스택
| 구분 | 기술 | 버전 | 역할 |
|------|------|------|------|
| 수집 | OpenTelemetry SDK (Python) | 1.40.0 | 에이전트에서 텔레메트리 생성 |
| 라우팅 | OpenTelemetry Collector | 0.149.0 | 텔레메트리 수집 및 백엔드 라우팅 |
| 메트릭 | Prometheus | v3.10.0 | 시계열 메트릭 저장/조회 |
| 로그 | Loki | 3.7.1 | 로그 수집/저장/조회 |
| 트레이스 | Tempo | 2.6.1 | 분산 트레이스 저장/조회 |
| 시각화 | Grafana | 12.4.2 | 대시보드, 알림, 탐색 |

---

## 2. 아키텍처

### 2.1 전체 구성도

```
┌─────────────────────────────────────────────────────────────────┐
│                        사용자 요청                                │
│                           │                                     │
│                           ▼                                     │
│                  ┌─────────────────┐                            │
│                  │   Orchestrator  │ :8000                       │
│                  │    (gpt-4.1)    │                             │
│                  └──┬────┬────┬───┘                             │
│                     │    │    │     tool call (병렬)              │
│            ┌────────┘    │    └────────┐                        │
│            ▼             ▼             ▼                        │
│     ┌───────────┐ ┌───────────┐ ┌───────────┐                  │
│     │  Search   │ │Summarizer │ │   Coder   │                  │
│     │   :8001   │ │   :8002   │ │   :8003   │                  │
│     │gpt-4.1-   │ │gpt-4.1-   │ │  gpt-4.1  │                  │
│     │   mini    │ │   mini    │ │           │                  │
│     └─────┬─────┘ └─────┬─────┘ └─────┬─────┘                  │
│           └──────────────┼─────────────┘                        │
│                          │  OTel SDK (gRPC)                     │
│                          ▼                                      │
│                 ┌─────────────────┐                             │
│                 │  OTel Collector │ :4317                        │
│                 └──┬────┬────┬───┘                              │
│                    │    │    │                                   │
│          ┌─────────┘    │    └─────────┐                        │
│          ▼              ▼              ▼                        │
│   ┌────────────┐ ┌────────────┐ ┌────────────┐                 │
│   │ Prometheus │ │    Loki    │ │   Tempo    │                  │
│   │   :9090    │ │   :3100    │ │   :3200    │                  │
│   │  메트릭    │ │    로그    │ │  트레이스   │                  │
│   └──────┬─────┘ └──────┬─────┘ └──────┬─────┘                 │
│          └──────────────┼──────────────┘                        │
│                         ▼                                       │
│                ┌─────────────────┐                              │
│                │    Grafana      │ :3000                         │
│                │ 대시보드 / 알림  │                               │
│                └─────────────────┘                              │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 데이터 흐름

```
에이전트 (Python App)
  │
  ├─ Traces  ──► OTel Collector ──► Tempo    ──► Grafana
  ├─ Metrics ──► OTel Collector ──► Prometheus ─► Grafana
  └─ Logs    ──► OTel Collector ──► Loki     ──► Grafana
```

| 텔레메트리 | 프로토콜 | 포맷 |
|-----------|---------|------|
| Traces | gRPC (OTLP) | OTLP → Tempo 내부 포맷 |
| Metrics | gRPC (OTLP) | OTLP → Prometheus exposition |
| Logs | gRPC (OTLP) | OTLP → Loki HTTP push |

### 2.3 데이터 영속화 (Volume Mount)

```
호스트: ../lgtm_volume/
├── prometheus/   → 컨테이너 /prometheus
├── loki/         → 컨테이너 /loki
├── tempo/        → 컨테이너 /tmp/tempo
└── grafana/      → 컨테이너 /var/lib/grafana
```

컨테이너를 재시작/재생성해도 메트릭, 로그, 트레이스, 대시보드 데이터가 보존됨.

---

## 3. 계측 (Instrumentation) 설계

### 3.1 자동 계측 (Auto-Instrumentation)

| 라이브러리 | 대상 | 수집 항목 |
|-----------|------|----------|
| `FastAPIInstrumentor` | HTTP 수신 요청 | 요청 duration, status code, route |
| `HTTPXClientInstrumentor` | HTTP 발신 요청 | sub-agent 호출 duration, status |

### 3.2 수동 계측 (Custom Instrumentation)

#### 3.2.1 Span 구조 (Trace)

하나의 사용자 요청이 생성하는 Span 트리:

```
TraceID: xxxxxxxx (요청 1건 = TraceID 1개)
│
├─ Span: agent-run                    ← 최상위 (orchestrator)
│  ├─ agent.type = "orchestrator"
│  ├─ request.query = "사용자 질문"
│  │
│  ├─ Span: llm-call                  ← 1차 LLM 호출 (도구 선택)
│  │  ├─ llm.model = "gpt-4.1"
│  │  ├─ llm.prompt = "사용자 질문"
│  │  ├─ llm.tool_calls = "call_search"
│  │  ├─ llm.prompt_tokens = 120
│  │  ├─ llm.completion_tokens = 25
│  │  ├─ llm.cost_usd = 0.0004
│  │  └─ llm.duration = 1.2
│  │
│  ├─ Span: sub-agent-call            ← Sub-Agent HTTP 호출
│  │  ├─ sub_agent.name = "call_search"
│  │  ├─ sub_agent.url = "http://agent-search:8000"
│  │  ├─ sub_agent.status = "success"
│  │  │
│  │  └─ Span: agent-run              ← search 에이전트 내부
│  │     ├─ agent.type = "search"
│  │     ├─ cache.hit = false
│  │     │
│  │     └─ Span: llm-call            ← search의 LLM 호출
│  │        ├─ llm.model = "gpt-4.1-mini"
│  │        ├─ llm.prompt = "사용자 질문"
│  │        ├─ llm.response = "응답 내용"
│  │        ├─ llm.prompt_tokens = 44
│  │        ├─ llm.completion_tokens = 80
│  │        └─ llm.cost_usd = 0.0001
│  │
│  └─ Span: llm-call                  ← 2차 LLM 호출 (최종 종합)
│     ├─ llm.model = "gpt-4.1"
│     ├─ llm.response = "최종 응답"
│     └─ llm.cost_usd = 0.0003
```

#### 3.2.2 Span Attribute 명세

| Span | Attribute | 타입 | 설명 |
|------|-----------|------|------|
| `agent-run` | `agent.type` | string | orchestrator / search / summarizer / coder |
| | `request.query` | string | 사용자 질문 (최대 200자) |
| | `param.*` | string | 사용자 파라미터 (user_id, session_id 등) |
| | `cache.hit` | bool | 캐시 적중 여부 (sub-agent만) |
| | `quota.rejected` | bool | quota 초과로 거부됨 |
| | `orchestrator.agents_called` | string | 호출된 sub-agent 목록 (쉼표 구분) |
| `llm-call` | `llm.model` | string | 사용 모델 (gpt-4.1, gpt-4.1-mini) |
| | `llm.prompt` | string | 프롬프트 내용 (최대 500자) |
| | `llm.response` | string | 응답 내용 (최대 500자) |
| | `llm.tool_calls` | string | 호출된 도구명 (쉼표 구분) |
| | `llm.prompt_tokens` | int | 프롬프트 토큰 수 |
| | `llm.completion_tokens` | int | 완성 토큰 수 |
| | `llm.total_tokens` | int | 총 토큰 수 |
| | `llm.duration` | float | LLM 호출 소요 시간 (초) |
| | `llm.cost_usd` | float | 호출 비용 (USD) |
| | `llm.retries` | int | 재시도 횟수 |
| `sub-agent-call` | `sub_agent.name` | string | 호출 대상 (call_search 등) |
| | `sub_agent.url` | string | 대상 URL |
| | `sub_agent.status` | string | success / error |

---

## 4. 메트릭 설계

### 4.1 커스텀 메트릭 목록

| 메트릭 이름 | 타입 | 단위 | 라벨 | 설명 |
|------------|------|------|------|------|
| `agent.run.count` | Counter | - | `agent.type` | 에이전트 실행 횟수 |
| `agent.error.count` | Counter | - | `agent.type`, `error.type` | 에이전트 에러 횟수 |
| `llm.call.duration` | Histogram | s | `llm.model`, `agent.type` | LLM 호출 지연시간 |
| `llm.token.usage` | Counter | - | `llm.model`, `type` (prompt/completion) | 토큰 사용량 |
| `llm.cost.usd` | Counter | USD | `llm.model`, `agent.type` | LLM 비용 |
| `llm.tokens.per_request` | Histogram | - | `llm.model`, `agent.type` | 요청당 토큰 분포 |
| `llm.rate_limit.count` | Counter | - | `llm.model`, `agent.type` | 429 Rate Limit 횟수 |
| `llm.retry.count` | Counter | - | `llm.model`, `reason` | 재시도 횟수 |
| `cache.hit.count` | Counter | - | `agent.type` | 캐시 적중 횟수 |
| `cache.miss.count` | Counter | - | `agent.type` | 캐시 미스 횟수 |
| `quota.reject.count` | Counter | - | `agent.type` | Quota 초과 거부 횟수 |

### 4.2 모델별 가격 정보 (메트릭 비용 계산 기준)

| 모델 | Prompt ($/1M tokens) | Completion ($/1M tokens) |
|------|---------------------|-------------------------|
| gpt-4.1 | $2.00 | $8.00 |
| gpt-4.1-mini | $0.40 | $1.60 |

### 4.3 주요 PromQL 쿼리

```promql
# 에이전트별 초당 요청 수
rate(agent_run_count_total[5m])

# 에러율 (%)
rate(agent_error_count_total[5m]) / rate(agent_run_count_total[5m]) * 100

# LLM 호출 지연시간 (P50 / P95 / P99)
histogram_quantile(0.50, rate(llm_call_duration_seconds_bucket[5m]))
histogram_quantile(0.95, rate(llm_call_duration_seconds_bucket[5m]))
histogram_quantile(0.99, rate(llm_call_duration_seconds_bucket[5m]))

# 모델별 토큰 사용 속도
rate(llm_token_usage_total[5m])

# 누적 비용
llm_cost_usd_total

# 분당 비용 증가율
rate(llm_cost_usd_total[5m]) * 60

# 캐시 히트율 (%)
rate(cache_hit_count_total[5m]) / (rate(cache_hit_count_total[5m]) + rate(cache_miss_count_total[5m])) * 100

# 요청당 평균 토큰
sum(llm_token_usage_total) / sum(agent_run_count_total)

# 캐시로 절약한 비용 추정
sum(cache_hit_count_total) * (sum(llm_cost_usd_total) / sum(agent_run_count_total))
```

---

## 5. 로그 설계

### 5.1 로그 수집 경로

```
Python logging → OTel LoggingHandler → OTel Collector → Loki
```

### 5.2 로그 레벨별 이벤트

| 레벨 | 이벤트 | 포함 필드 |
|------|--------|----------|
| INFO | Agent run started | agent_type, query (100자), params |
| INFO | LLM call completed | model, prompt_tokens, completion_tokens, duration, cost_usd, retries |
| INFO | Sub-agent call completed | tool, status |
| INFO | Cache hit | agent_type, query (80자) |
| WARNING | Rate limit 429 | model, retry_after |
| WARNING | API error | model, status_code |
| WARNING | Quota exceeded | agent_type, reason |
| ERROR | Agent run failed | agent_type, error |
| ERROR | Sub-agent call failed | tool, error |

### 5.3 주요 LogQL 쿼리

```logql
# 전체 에이전트 로그
{service_name=~"agent-.*"}

# 에러 로그만
{service_name=~"agent-.*"} |= "ERROR"

# Rate Limit 관련
{service_name=~"agent-.*"} |= "rate_limit" or |= "429"

# 특정 에이전트 로그
{service_name="agent-orchestrator"}
```

---

## 6. 분산 트레이스 설계

### 6.1 Trace 전파 (Context Propagation)

```
Orchestrator                      Search Agent
    │                                  │
    ├─ OTel Context (traceparent)  ──► │
    │   via HTTP Header                │
    │                                  ├─ 동일 TraceID로 Span 생성
    │   ◄── HTTP Response ────────────┘
```

- FastAPI/HTTPX instrumentor가 자동으로 `traceparent` 헤더를 전파
- Sub-Agent에서 생성된 Span이 동일 TraceID에 연결됨
- Grafana Tempo에서 하나의 TraceID로 전체 흐름을 조회 가능

### 6.2 Tempo 데이터소스 연동

| 기능 | 연동 대상 | 설명 |
|------|----------|------|
| Trace → Log | Loki | TraceID/SpanID로 관련 로그 자동 필터링 |
| Trace → Metric | Prometheus | span-metrics로 자동 생성된 메트릭 연결 |
| Service Graph | Prometheus | 서비스 간 호출 관계 자동 시각화 |
| Node Graph | Tempo 내부 | Span 간 의존성 그래프 |

### 6.3 Tempo metrics-generator 설정

```yaml
metrics_generator:
  processors: [service-graphs, span-metrics, local-blocks]
  storage:
    remote_write:
      - url: http://prometheus:9090/api/v1/write
        send_exemplars: true
```

- **service-graphs**: 서비스 간 호출 관계 메트릭 자동 생성
- **span-metrics**: span 기반 RED 메트릭 (Rate, Error, Duration) 자동 생성
- **exemplars**: 메트릭 → 트레이스 연결 (Grafana에서 메트릭 그래프 클릭 시 관련 트레이스로 이동)

---

## 7. 대시보드 설계

### 7.1 대시보드: Multi-Agent Overview

| 패널 | 타입 | 데이터소스 | 표시 내용 |
|------|------|----------|----------|
| Agent Run Rate | timeseries | Prometheus | 에이전트별 초당 요청 수 |
| Error Rate (%) | timeseries | Prometheus | 에이전트별 에러율 |
| LLM Call Duration | timeseries | Prometheus | P50/P95/P99 지연시간 |
| Token Usage Rate | timeseries | Prometheus | 모델별 토큰 사용 속도 |
| Cumulative Cost | timeseries | Prometheus | 누적 비용 (USD) |
| Cost Rate | timeseries | Prometheus | 분당 비용 증가율 |
| Total Requests | stat | Prometheus | 총 요청 수 |
| Total Tokens | stat | Prometheus | 총 토큰 수 |
| Total Cost | stat | Prometheus | 총 비용 |
| Avg Tokens/Request | stat | Prometheus | 요청당 평균 토큰 |
| Tokens Distribution | histogram | Prometheus | 요청당 토큰 분포 |
| Recent Agent Logs | logs | Loki | 최근 에이전트 로그 |
| Rate Limit Hits | timeseries | Prometheus | 429 발생 횟수 |
| Retry Count | timeseries | Prometheus | 재시도 횟수 |
| Cache Hit Rate | timeseries | Prometheus | 캐시 히트율 (%) |
| Cache Hit/Miss | stat | Prometheus | 캐시 적중/미스 누적 |
| Total Rate Limits | stat | Prometheus | 총 Rate Limit 수 |
| Total Retries | stat | Prometheus | 총 재시도 수 |
| Cost Saved by Cache | stat | Prometheus | 캐시로 절약한 비용 |
| Model Avg Duration | bargauge | Prometheus | 모델별 평균 지연시간 비교 |
| Model Cost/Request | bargauge | Prometheus | 모델별 요청당 비용 비교 |
| Error Logs | logs | Loki | 에러/Rate Limit 로그 |

### 7.2 대시보드 레이아웃

```
Row 1: [Agent Run Rate        ] [Error Rate (%)         ]
Row 2: [LLM Call Duration     ] [Token Usage Rate       ]
Row 3: [Cumulative Cost       ] [Cost Rate (USD/min)    ]
Row 4: [Total Reqs][Total Tokens][Total Cost][Avg Tokens]
Row 5: [Tokens Distribution   ] [Recent Agent Logs      ]
Row 6: [Rate Limit][Retry Count ] [Cache Hit Rate       ]
Row 7: [Cache Hit/Miss][Rate Limits][Retries][Cost Saved]
Row 8: [Model Avg Duration    ] [Model Cost per Request ]
Row 9: [                 Error Logs                      ]
```

---

## 8. 알림 (Alerting) 설계

### 8.1 알림 규칙

| 알림 | 조건 | 심각도 | 대기시간 |
|------|------|--------|---------|
| Cost Spike | 5분간 비용 > $0.05 | warning | 즉시 |
| High Error Rate | 에러율 > 10% | critical | 2분 |
| Rate Limit Hit | 429 발생 > 0 | warning | 즉시 |
| High Latency | P95 > 10초 | warning | 2분 |

### 8.2 알림 평가 주기

- 평가 간격: 1분
- 알림 그룹: `agent-alerts`

### 8.3 알림 상세

#### Cost Spike Alert
```promql
increase(llm_cost_usd_total[5m]) > 0.05
```
5분 내 LLM 비용이 $0.05를 초과하면 즉시 알림.
과도한 요청이나 비정상적인 토큰 사용을 조기 감지.

#### High Error Rate
```promql
(rate(agent_error_count_total[5m]) / rate(agent_run_count_total[5m])) * 100 > 10
```
에러율이 10%를 2분 이상 초과하면 critical 알림.
Azure OpenAI 장애나 네트워크 문제 감지.

#### Rate Limit Hit
```promql
increase(llm_rate_limit_count_total[5m]) > 0
```
429 에러 발생 시 즉시 알림.
Azure OpenAI TPM/RPM 한도 도달 감지.

#### High Latency
```promql
histogram_quantile(0.95, rate(llm_call_duration_seconds_bucket[5m])) > 10
```
P95 지연시간이 10초를 2분 이상 초과하면 알림.
모델 서버 과부하나 네트워크 지연 감지.

---

## 9. OTel Collector 설정

### 9.1 파이프라인 구성

```
receivers:                processors:            exporters:
  otlp (gRPC :4317)  ──►   batch           ──►   otlp/tempo (traces)
  otlp (HTTP :4318)  ──►   (1024/5s)       ──►   prometheus (metrics :8889)
                                            ──►   otlphttp/loki (logs)
```

### 9.2 Batch Processor 설정

| 설정 | 값 | 설명 |
|------|---|------|
| `send_batch_size` | 1024 | 한 번에 전송하는 텔레메트리 수 |
| `send_batch_max_size` | 2048 | 최대 배치 크기 |
| `timeout` | 5s | 배치가 차지 않아도 전송하는 최대 대기 시간 |

---

## 10. 캐시 모니터링

### 10.1 캐시 동작

```
요청 → cache_key(model + query.lower().strip()) → SHA256 해시
  ├─ 캐시 히트 → 즉시 응답 (비용 $0, 토큰 0)
  └─ 캐시 미스 → LLM 호출 → 결과 캐시 저장 → 응답
```

### 10.2 캐시 설정

| 설정 | 환경변수 | 기본값 | 설명 |
|------|---------|--------|------|
| TTL | `CACHE_TTL_SECONDS` | 300초 | 캐시 만료 시간 |
| 최대 크기 | `CACHE_MAX_SIZE` | 100건 | LRU 정책으로 초과 시 오래된 항목 제거 |

### 10.3 모니터링 지표

- **Cache Hit Rate**: 캐시 효율성 (목표: 30% 이상)
- **Cost Saved by Cache**: 캐시로 절약한 비용 추정
- **Cache Size**: `/stats` API에서 현재 캐시 크기 확인

---

## 11. Quota (사용량 제한) 모니터링

### 11.1 Quota 설정

| 환경변수 | 기본값 | 설명 |
|---------|--------|------|
| `USER_TOKEN_QUOTA` | 0 (무제한) | 사용자별 토큰 제한 |
| `USER_COST_QUOTA` | 0 (무제한) | 사용자별 비용 제한 (USD) |

### 11.2 Quota 초과 시 동작

1. 요청 거부 (HTTP 429)
2. `quota.reject.count` 메트릭 증가
3. Span attribute `quota.rejected=true` 기록
4. WARNING 로그 기록

---

## 12. 운영 API

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| `/health` | GET | 헬스 체크 (agent_type, service name) |
| `/stats` | GET | 통계 조회 (요청수, 토큰, 비용, 캐시, 모델별, 사용자별) |
| `/run` | POST | 에이전트 실행 |
| `/cache/clear` | POST | 캐시 초기화 |

---

## 13. 컨테이너 구성

| 컨테이너 | 이미지 | 포트 | 헬스체크 |
|---------|--------|------|---------|
| agent-orchestrator | 자체 빌드 | 8000 | `/health` (10s 간격) |
| agent-search | 자체 빌드 | 8001 | `/health` (10s 간격) |
| agent-summarizer | 자체 빌드 | 8002 | `/health` (10s 간격) |
| agent-coder | 자체 빌드 | 8003 | `/health` (10s 간격) |
| otel-collector | otel/opentelemetry-collector-contrib | 4317, 4318, 8889 | - |
| prometheus | prom/prometheus | 9090 | - |
| loki | grafana/loki | 3100 | - |
| tempo | grafana/tempo | 3200 | - |
| grafana | grafana/grafana | 3000 | - |
| loadgen | 자체 빌드 | - | - (최대 5분 실행 후 자동 종료) |

### 13.1 의존성 순서

```
prometheus, loki, tempo
        │
        ▼
  otel-collector
        │
        ▼
search, summarizer, coder  (healthy 대기)
        │
        ▼
   orchestrator  (healthy 대기)
        │
        ▼
     loadgen
```

---

## 14. 장애 대응 시나리오

### 14.1 Azure OpenAI Rate Limit (429)

| 단계 | 동작 |
|------|------|
| 감지 | `llm.rate_limit.count` 메트릭 증가 + Grafana 알림 |
| 자동 대응 | Exponential backoff + jitter로 재시도 (최대 3회) |
| 모니터링 | Tempo에서 `rate_limit_hit` 이벤트 확인 |
| 수동 대응 | Azure Portal에서 TPM/RPM 한도 증설 |

### 14.2 Sub-Agent 장애

| 단계 | 동작 |
|------|------|
| 감지 | `agent.error.count` 증가 + High Error Rate 알림 |
| 로그 확인 | Loki에서 `{service_name="agent-orchestrator"} \|= "Sub-agent call failed"` |
| 트레이스 확인 | Tempo에서 `sub_agent.status=error` span 조회 |
| 수동 대응 | 해당 agent 컨테이너 재시작 |

### 14.3 비용 급증

| 단계 | 동작 |
|------|------|
| 감지 | Cost Spike 알림 |
| 원인 분석 | 대시보드에서 모델별/에이전트별 비용 확인 |
| 트레이스 확인 | 비정상적으로 큰 토큰 사용 요청 추적 |
| 대응 | Quota 설정, 캐시 TTL 조정, loadgen 중지 |
