# LGTM Stack — AI Multi-Agent Observability

Azure OpenAI 기반 멀티에이전트 시스템의 Logs, Grafana, Traces, Metrics를 통합 모니터링하는 스택.

Orchestrator가 사용자 질의를 분석하여 sub-agent(Search, Summarizer, Coder)로 라우팅하고, 전체 호출 체인을 분산 트레이스로 추적한다.

## 아키텍처

```
  User / Loadgen
       │
       │ POST /run {"query":"...", "params":{...}}
       ▼
  ┌─────────────────────┐
  │  Orchestrator :8000 │  gpt-4.1
  │  라우팅 + 결과 종합   │  function calling
  └──────┬──────────────┘
         │ httpx (trace context 자동 전파)
    ┌────┼────────────┐
    ▼    ▼            ▼
  ┌──────┐ ┌────────┐ ┌───────┐
  │Search│ │Summary │ │ Coder │
  │ :8001│ │ :8002  │ │ :8003 │
  │ mini │ │  mini  │ │  4.1  │
  └──┬───┘ └───┬────┘ └──┬────┘
     │         │         │
     └────┬────┘─────────┘
          │ OTel (gRPC :4317)
          ▼
  ┌─────────────────────────────────────────────────────────┐
  │                   OTel Collector :4317                   │
  └──────┬──────────────┬───────────────┬───────────────────┘
         │ traces       │ logs          │ metrics
         ▼              ▼               ▼
  ┌────────────┐ ┌────────────┐ ┌──────────────┐
  │   Tempo    │ │    Loki    │ │  Prometheus   │
  │   :3200    │ │   :3100    │ │    :9090      │
  └──────┬─────┘ └─────┬──────┘ └──────┬───────┘
         └─────────┬───┘───────────────┘
                   ▼
            ┌────────────┐
            │  Grafana   │
            │   :3000    │
            └────────────┘
```

## 서비스 구성

| 서비스 | 포트 | 역할 | 모델 |
|--------|------|------|------|
| **agent-orchestrator** | 8000 | 질의 분석 → sub-agent 라우팅 → 결과 종합 | gpt-4.1 |
| **agent-search** | 8001 | 팩트/지식 질의 응답 | gpt-4.1-mini |
| **agent-summarizer** | 8002 | 텍스트 요약 | gpt-4.1-mini |
| **agent-coder** | 8003 | 코드 생성/리뷰 | gpt-4.1 |
| **otel-collector** | 4317/4318 | 텔레메트리 수집 → 백엔드 라우팅 | - |
| **tempo** | 3200 | 분산 트레이스 저장소 | - |
| **loki** | 3100 | 로그 저장소 | - |
| **prometheus** | 9090 | 메트릭 저장소 | - |
| **grafana** | 3000 | 대시보드/조회 | - |
| **loadgen** | - | 자동 트래픽 생성 (15개 질의, 30% Heavy) | - |

## 데이터 흐름

| 파이프라인 | 경로 | 내용 |
|-----------|------|------|
| **Traces** | Agent → OTel → Tempo → Grafana | span hierarchy, params, 토큰 수, 지연시간 |
| **Logs** | Agent → OTel → Loki → Grafana | LLM 호출, 에러, 비용 로그 |
| **Metrics** | Agent → OTel → Prometheus → Grafana | 실행 횟수, 토큰 사용량, P95 지연 |
| **Cost** | Agent `/stats` API | 모델별 누적 토큰/비용 |

## 요청 흐름 (Trace 구조)

```
[orchestrator] POST /run
  └─ agent-run (param.user_id=..., param.session_id=...)
       ├─ llm-call (gpt-4.1, 라우팅 결정)
       ├─ sub-agent-call → [search] POST /run
       │                      └─ agent-run (params 전파됨)
       │                           └─ llm-call (gpt-4.1-mini)
       └─ llm-call (gpt-4.1, 최종 종합)
```

W3C Trace Context가 `HTTPXClientInstrumentor`에 의해 자동 전파되어, orchestrator → sub-agent 호출이 하나의 trace로 연결된다.

## 프로젝트 구조

```
lgtm/
├── docker-compose.yml          # 전체 스택 (모니터링 + Agent 4개 + LoadGen)
├── .env                        # AOAI 인증 정보 (git 미포함)
├── yamls/
│   ├── otel-config.yaml        # OTel Collector 설정
│   ├── prometheus.yml          # Prometheus scrape 설정
│   └── tempo.yaml              # Tempo 트레이스 저장소 설정
├── Makefile                    # 실행/테스트/조회 명령어
├── grafana/
│   └── provisioning/
│       └── datasources/
│           └── datasources.yaml
├── agent/                      # 멀티에이전트 (Orchestrator + Sub-agents)
│   ├── app.py                  # FastAPI + AOAI + OTel 계측
│   ├── requirements.txt
│   └── Dockerfile
├── loadgen/                    # 자동 트래픽 생성기
│   ├── run.py
│   └── Dockerfile
├── tests/                      # 통합 테스트
│   ├── test_agents.py
│   └── requirements.txt
└── docs/
    └── observability-guide.md  # 상세 관측 가이드
```

## 빠른 시작

```bash
# 1. .env 파일 설정 (AOAI 인증 정보)
cp .env.example .env  # 또는 직접 편집

# 2. 전체 스택 실행
make up

# 3. 헬스체크
make health

# 4. 테스트
make test-orchestrator

# 5. Grafana 접속
open http://localhost:3000
```

## API 사용법

### 기본 요청

```bash
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"query": "What is Kubernetes?"}'
```

### 커스텀 파라미터 전달 (Grafana Trace에서 조회 가능)

```bash
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Write a binary search in Python",
    "params": {
      "user_id": "user-123",
      "session_id": "sess-abc",
      "priority": "high",
      "department": "engineering"
    }
  }'
```

`params`의 key-value는 span attribute `param.{key}`로 기록되어 Tempo에서 검색 가능:
```
{ span.param.user_id = "user-123" }
```

### 응답 예시

```json
{
  "agent_type": "orchestrator",
  "model": "gpt-4.1",
  "result": "...",
  "tokens": {"prompt": 452, "completion": 166},
  "cost_usd": 0.002232
}
```

### Sub-agent 직접 호출

```bash
curl -X POST http://localhost:8001/run -H "Content-Type: application/json" -d '{"query": "What is Docker?"}'    # search
curl -X POST http://localhost:8002/run -H "Content-Type: application/json" -d '{"query": "Summarize: ..."}'      # summarizer
curl -X POST http://localhost:8003/run -H "Content-Type: application/json" -d '{"query": "Write a hello world"}' # coder
```

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `AZURE_OPENAI_API_KEY` | - | AOAI API 키 |
| `AZURE_OPENAI_ENDPOINT` | - | AOAI 엔드포인트 |
| `AZURE_OPENAI_API_VERSION` | `2024-12-01-preview` | API 버전 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTel Collector gRPC |
| `OTEL_SERVICE_NAME` | `ai-agent` | 서비스 식별명 |
| `AGENT_TYPE` | `default` | `orchestrator` / `search` / `summarizer` / `coder` |

## 수집되는 텔레메트리

### Traces (Tempo)

| Span | Attributes |
|------|-----------|
| `agent-run` | `agent.type`, `request.query`, `param.*` (커스텀 파라미터) |
| `llm-call` | `llm.model`, `llm.prompt_tokens`, `llm.completion_tokens`, `llm.total_tokens`, `llm.duration` |
| `sub-agent-call` | `sub_agent.name`, `sub_agent.url`, `sub_agent.status` |

### Metrics (Prometheus)

| 메트릭 | 타입 | Labels |
|--------|------|--------|
| `agent.run.count` | Counter | `agent.type` |
| `agent.error.count` | Counter | `agent.type` |
| `llm.call.duration` | Histogram | `agent.type`, `llm.model` |
| `llm.token.usage` | Counter | `llm.model`, `type` (prompt/completion) |

### Logs (Loki)

| 메시지 | 레벨 | 포함 정보 |
|--------|------|----------|
| `Agent run started` | INFO | agent_type, query, params |
| `LLM call completed` | INFO | model, tokens, duration, cost_usd |
| `Sub-agent call completed/failed` | INFO/ERROR | tool, status, error |

## Grafana에서 조회

### Traces (Tempo)

```
# 특정 사용자의 트레이스
{ span.param.user_id = "user-123" }

# 느린 LLM 호출
{ name = "llm-call" && duration > 2s }

# 특정 모델
{ span.llm.model = "gpt-4.1" }

# 에러 트레이스
{ status = error }
```

### Metrics (Prometheus)

```promql
# 에이전트별 RPS
rate(agent_run_count_total[5m])

# LLM P95 지연시간
histogram_quantile(0.95, rate(llm_call_duration_seconds_bucket[5m]))

# 모델별 토큰 사용률
rate(llm_token_usage_total[5m])
```

### Logs (Loki)

```logql
# 전체 에이전트 로그
{service_name=~"agent-.*"}

# 에러만
{service_name=~"agent-.*"} |= "ERROR"

# LLM 호출 로그
{service_name=~"agent-.*"} |= "LLM call completed"
```

## Makefile 명령어

| 명령어 | 설명 |
|--------|------|
| `make up` | 전체 스택 빌드 + 실행 |
| `make down` | 전체 중지 |
| `make restart` | 재시작 |
| `make status` | 서비스 상태 |
| `make health` | 4개 에이전트 헬스체크 |
| `make test-orchestrator` | orchestrator 테스트 |
| `make test-search` | search 직접 테스트 |
| `make test-summarizer` | summarizer 직접 테스트 |
| `make test-coder` | coder 직접 테스트 |
| `make test-all` | 전체 테스트 |
| `make stats` | 에이전트별 토큰/비용 상세 |
| `make stats-all` | 비용 한줄 요약 |
| `make logs` | 전체 Docker 로그 |
| `make logs-agents` | 에이전트 로그만 |
| `make logs-loki` | Loki 최근 5분 로그 |
| `make logs-errors` | Loki 에러 로그 |
| `make query-metrics` | Prometheus 메트릭 조회 |
| `make query-traces` | Tempo 최근 트레이스 |

## 비용 추적

각 에이전트의 `/stats` 엔드포인트에서 실시간 누적 비용을 확인할 수 있다.

```bash
curl http://localhost:8000/stats | python3 -m json.tool
```

```json
{
  "agent_type": "orchestrator",
  "uptime_seconds": 120.5,
  "total_requests": 15,
  "total_tokens": {"prompt": 6450, "completion": 3360, "total": 9810},
  "total_cost_usd": 0.039780,
  "by_model": {
    "gpt-4.1": {
      "calls": 15,
      "prompt_tokens": 6450,
      "completion_tokens": 3360,
      "cost_usd": 0.039780,
      "avg_tokens_per_call": 654.0
    }
  },
  "pricing_per_1m_tokens": {
    "gpt-4.1":      {"prompt": 2.00, "completion": 8.00},
    "gpt-4.1-mini": {"prompt": 0.40, "completion": 1.60}
  }
}
```
