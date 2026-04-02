# Observability Guide — LGTM 멀티에이전트 모니터링

## 개요

4개의 AI 에이전트(orchestrator, search, summarizer, coder)가 생성하는 텔레메트리 데이터를 Grafana에서 조회하는 방법을 설명한다.

```
Agent → OTel Collector → Tempo (traces) / Loki (logs) / Prometheus (metrics) → Grafana
```

---

## 1. Traces (Tempo)

### 접근 방법
Grafana → Explore → 데이터소스: **Tempo**

### 수집되는 Span

| Span 이름 | 발생 위치 | 설명 |
|-----------|----------|------|
| `GET /health`, `POST /run` | FastAPI 자동 계측 | HTTP 요청 루트 span |
| `agent-run` | 모든 에이전트 | 에이전트 실행 전체를 감싸는 span |
| `llm-call` | 모든 에이전트 | Azure OpenAI API 호출 1회 |
| `sub-agent-call` | orchestrator만 | orchestrator → sub-agent HTTP 호출 |
| `HTTP POST` | orchestrator만 | httpx 자동 계측, sub-agent로의 아웃바운드 요청 |

### Span Attributes (파라미터)

**Tempo에서 파라미터(attribute) 보는 방법:**
1. Grafana → Explore → Tempo
2. Search → `Service Name` = `agent-orchestrator` 등으로 검색
3. 트레이스 ID 클릭 → 각 span 클릭 → **Tags** 섹션에서 attribute 확인

| Attribute | 타입 | 예시 | 설명 |
|-----------|------|------|------|
| `agent.type` | string | `orchestrator`, `search`, `coder` | 에이전트 종류 |
| `service.name` | string | `agent-orchestrator` | Resource attribute (서비스 식별) |
| `llm.model` | string | `gpt-4.1`, `gpt-4.1-mini` | 사용된 AOAI 모델 |
| `llm.prompt_tokens` | int | `318` | 입력 토큰 수 |
| `llm.completion_tokens` | int | `25` | 출력 토큰 수 |
| `llm.total_tokens` | int | `343` | 총 토큰 수 |
| `llm.duration` | float | `1.234` | LLM 호출 소요 시간 (초) |
| `llm.message_count` | int | `2` | 메시지 배열 길이 |
| `sub_agent.name` | string | `call_search` | 호출된 sub-agent 함수명 |
| `sub_agent.url` | string | `http://agent-search:8000` | sub-agent URL |
| `sub_agent.status` | string | `success`, `error` | 호출 결과 |
| `orchestrator.agents_called` | string | `call_search,call_coder` | orchestrator가 호출한 에이전트 목록 |
| `error` | bool | `true` | 에러 발생 여부 |

### Distributed Trace 예시

```
[orchestrator] POST /run (root)
  └─ agent-run
       ├─ llm-call (gpt-4.1 — routing decision)
       ├─ sub-agent-call (call_search)
       │   └─ HTTP POST http://agent-search:8000/run
       │       └─ [search] POST /run
       │            └─ agent-run
       │                 └─ llm-call (gpt-4.1-mini)
       └─ llm-call (gpt-4.1 — final aggregation)
```

Tempo의 Node Graph 뷰에서 서비스 간 호출 관계를 시각적으로 확인할 수 있다.

### TraceQL 쿼리 예시

```
# 특정 에이전트의 트레이스
{ resource.service.name = "agent-orchestrator" }

# LLM 호출만 필터
{ name = "llm-call" }

# 느린 LLM 호출 (2초 이상)
{ name = "llm-call" && duration > 2s }

# 에러가 발생한 span
{ status = error }

# 특정 모델 사용 트레이스
{ span.llm.model = "gpt-4.1" }

# orchestrator가 여러 에이전트를 호출한 경우
{ span.orchestrator.agents_called =~ ".*,.*" }
```

---

## 2. Metrics (Prometheus)

### 접근 방법
Grafana → Explore → 데이터소스: **Prometheus**

### 수집되는 메트릭

| 메트릭 이름 | 타입 | Labels | 설명 |
|------------|------|--------|------|
| `agent_run_count_total` | Counter | `agent_type` | 에이전트 실행 총 횟수 |
| `agent_error_count_total` | Counter | `agent_type` | 에이전트 에러 횟수 |
| `llm_call_duration_seconds` | Histogram | `agent_type`, `llm_model` | LLM 호출 지연 시간 분포 |
| `llm_token_usage_total` | Counter | `llm_model`, `type` (`prompt`/`completion`) | 누적 토큰 사용량 |

### PromQL 쿼리 예시

```promql
# 에이전트별 초당 요청률 (RPS)
rate(agent_run_count_total[5m])

# 에이전트별 에러율
rate(agent_error_count_total[5m]) / rate(agent_run_count_total[5m])

# LLM 호출 평균 지연시간
rate(llm_call_duration_seconds_sum[5m]) / rate(llm_call_duration_seconds_count[5m])

# LLM 호출 P95 지연시간
histogram_quantile(0.95, rate(llm_call_duration_seconds_bucket[5m]))

# 모델별 토큰 사용률 (tokens/sec)
rate(llm_token_usage_total[5m])

# gpt-4.1의 prompt token 사용량
llm_token_usage_total{llm_model="gpt-4.1", type="prompt"}

# Tempo에서 자동 생성되는 서비스 메트릭 (tempo metrics_generator)
# - traces_spanmetrics_calls_total: span 호출 횟수
# - traces_spanmetrics_latency_bucket: span 지연 분포
# - traces_service_graph_request_total: 서비스 간 요청 횟수
```

### Tempo Metrics Generator

`tempo.yaml`에 `metrics_generator`가 설정되어 있어, Tempo가 트레이스로부터 자동으로 다음 메트릭을 Prometheus에 push한다:

| 자동 생성 메트릭 | 설명 |
|-----------------|------|
| `traces_spanmetrics_calls_total` | span별 호출 횟수 |
| `traces_spanmetrics_latency_bucket` | span 지연 분포 (히스토그램) |
| `traces_service_graph_request_total` | 서비스 간 호출 횟수 (service graph) |
| `traces_service_graph_request_failed_total` | 실패한 서비스 간 호출 |
| `traces_service_graph_request_server_seconds_bucket` | 서비스 간 지연 분포 |

---

## 3. Logs (Loki)

### 접근 방법
Grafana → Explore → 데이터소스: **Loki**

### 수집되는 로그

에이전트의 Python logging이 OTel LoggingHandler를 통해 Loki로 전송된다.

| 로그 메시지 | 레벨 | 포함 정보 |
|------------|------|----------|
| `Agent run started` | INFO | `agent_type`, `query` (앞 100자) |
| `LLM call completed` | INFO | `model`, `prompt_tokens`, `completion_tokens`, `duration`, `cost_usd` |
| `Sub-agent call completed` | INFO | `tool` (function name), `status` |
| `Sub-agent call failed` | ERROR | `tool`, `error` |
| `Agent run failed` | ERROR | `agent_type`, `error` |

### LogQL 쿼리 예시

```logql
# 특정 서비스의 모든 로그
{service_name="agent-orchestrator"}

# 에러 로그만
{service_name=~"agent-.*"} |= "ERROR"

# LLM 호출 로그만
{service_name=~"agent-.*"} |= "LLM call completed"

# 비용 정보가 포함된 로그 파싱
{service_name=~"agent-.*"} |= "cost_usd" | json | cost_usd > 0.001

# 느린 LLM 호출 (duration > 3초)
{service_name=~"agent-.*"} |= "LLM call completed" | json | duration > 3
```

### Trace-Log 연동

Loki 로그에는 OTel이 자동으로 `traceID`와 `spanID`를 삽입한다. Grafana의 Tempo 데이터소스에 `tracesToLogsV2` 설정이 되어 있어:
- Tempo에서 트레이스 클릭 → "Logs for this span" 버튼으로 해당 span의 로그를 바로 조회 가능
- Loki에서 로그 클릭 → traceID 링크로 관련 트레이스로 점프 가능

---

## 4. 비용 추적 (Cost Tracking)

### API 엔드포인트

각 에이전트의 `/stats` 엔드포인트에서 실시간 비용을 확인할 수 있다.

```bash
# 개별 에이전트
curl http://localhost:8000/stats   # orchestrator
curl http://localhost:8001/stats   # search
curl http://localhost:8002/stats   # summarizer
curl http://localhost:8003/stats   # coder

# 전체 요약
make stats      # 4개 에이전트 각각의 상세 stats
make stats-all  # 한 줄 요약
```

### 응답 예시

```json
{
  "agent_type": "search",
  "uptime_seconds": 120.5,
  "total_requests": 15,
  "total_tokens": { "prompt": 645, "completion": 3360, "total": 4005 },
  "total_cost_usd": 0.005634,
  "by_model": {
    "gpt-4.1-mini": {
      "prompt_tokens": 645,
      "completion_tokens": 3360,
      "cost_usd": 0.005634,
      "calls": 15,
      "avg_tokens_per_call": 267.0
    }
  },
  "pricing_per_1m_tokens": {
    "gpt-4.1":      { "prompt": 2.00, "completion": 8.00 },
    "gpt-4.1-mini": { "prompt": 0.40, "completion": 1.60 }
  }
}
```

### 각 요청의 비용

`POST /run` 응답에 `cost_usd` 필드가 포함된다:
```json
{
  "agent_type": "orchestrator",
  "result": "...",
  "tokens": { "prompt": 318, "completion": 25 },
  "cost_usd": 0.000836
}
```

### 가격 기준 (Azure OpenAI)

| 모델 | Input (per 1M tokens) | Output (per 1M tokens) |
|------|----------------------|----------------------|
| gpt-4.1 | $2.00 | $8.00 |
| gpt-4.1-mini | $0.40 | $1.60 |

---

## 5. Grafana 대시보드 활용 팁

### Service Map (Tempo → Node Graph)
Grafana → Explore → Tempo → "Service graph" 탭에서 서비스 간 호출 관계를 시각적으로 확인:
- `agent-orchestrator` → `agent-search`, `agent-summarizer`, `agent-coder`

### 추천 대시보드 패널

| 패널 | 데이터소스 | 쿼리 |
|------|----------|------|
| 에이전트별 RPS | Prometheus | `rate(agent_run_count_total[5m])` |
| LLM P95 지연 | Prometheus | `histogram_quantile(0.95, rate(llm_call_duration_seconds_bucket[5m]))` |
| 에러율 | Prometheus | `rate(agent_error_count_total[5m]) / rate(agent_run_count_total[5m])` |
| 토큰 사용률 | Prometheus | `rate(llm_token_usage_total[5m])` |
| 최근 에러 로그 | Loki | `{service_name=~"agent-.*"} \|= "ERROR"` |
| 트레이스 검색 | Tempo | `{ resource.service.name =~ "agent-.*" }` |
