# LGTM Stack — AI Multi-Agent Observability

Azure OpenAI 기반 멀티에이전트 시스템의 Logs, Grafana, Traces, Metrics를 통합 모니터링하는 스택.

Orchestrator가 사용자 질의를 분석하여 sub-agent(Search, Summarizer, Coder)로 라우팅하고, 전체 호출 체인을 분산 트레이스로 추적한다.

**두 가지 실행 모드를 지원한다:**
- **기본 모드** (`docker-compose.yml`) — 직접 HTTP 통신, 인메모리 캐시
- **Dapr 모드** (`docker-compose.dapr.yml`) — Dapr sidecar 기반 서비스 호출, Redis 분산 캐시, Pub/Sub 이벤트

## 아키텍처

### 기본 모드

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

### Dapr 모드

```
  User / Loadgen
       │
       ▼
  ┌──────────────────── network_mode 공유 (Pod-like) ─────────────────┐
  │  agent-orchestrator :8000        orchestrator-dapr :3500           │
  │  ┌──────────────────────┐       ┌───────────────────────────┐     │
  │  │  FastAPI App         │←────→ │  daprd sidecar            │     │
  │  │  1. LLM 호출 (AOAI)  │ local │  Service Invocation       │─────┼──→ sub-agents
  │  │  2. Tool call 결정   │ host  │  Pub/Sub 이벤트 발행       │─────┼──→ Redis
  │  │  3. 결과 합성        │       │  State Store 캐시 조회     │     │
  │  └──────────────────────┘       └───────────────────────────┘     │
  └───────────────────────────────────────────────────────────────────┘
       │ Dapr Service Invocation (mDNS, 자동 디스커버리)
       ├──────────────────────────┬──────────────────────────┐
       ▼                          ▼                          ▼
  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
  │ agent-search      │    │ agent-summarizer  │    │ agent-coder      │
  │ + search-dapr     │    │ + summarizer-dapr │    │ + coder-dapr     │
  │                   │    │                   │    │                   │
  │ Cache: Dapr State │    │ Cache: Dapr State │    │ Cache: Dapr State │
  │ Events: Pub/Sub   │    │ Events: Pub/Sub   │    │ Events: Pub/Sub   │
  └────────┬──────────┘    └────────┬──────────┘    └────────┬──────────┘
           └────────────────────────┼────────────────────────┘
                                    ▼
  ┌────────────────────── Redis :6381 ─────────────────────────────┐
  │  State Store (캐시)                   Pub/Sub (이벤트)          │
  │  agent-search||{hash} → 캐시 데이터    topic: agent-events      │
  │  agent-coder||{hash}  → 캐시 데이터    → 모든 agent가 구독       │
  └────────────────────────────────────────────────────────────────┘
           │
           ▼
  ┌─── Dapr 플랫폼 정책 (플랫폼 엔지니어 관리) ───────────────────┐
  │  Resiliency   → Retry 3회, Timeout 30s, Circuit Breaker       │
  │  Access Control → Orchestrator만 sub-agent 호출 가능           │
  │  Secret Store → AOAI 키를 Secret Store에서 조회 (평문 노출 X)  │
  └────────────────────────────────────────────────────────────────┘
           │
           ▼
  ┌─── Observability (LGTM Stack, 기본 모드와 동일) ───┐
  │  OTel → Prometheus + Loki + Tempo → Grafana        │
  │  대시보드: Agent Overview / Cost Tracker / Dapr Health│
  └────────────────────────────────────────────────────┘
```

## 서비스 구성

### 공통 서비스

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

### Dapr 모드 추가 서비스

| 서비스 | 포트 | 역할 |
|--------|------|------|
| **orchestrator-dapr** | - | orchestrator용 Dapr sidecar (`network_mode: service:agent-orchestrator`) |
| **search-dapr** | - | search용 Dapr sidecar |
| **summarizer-dapr** | - | summarizer용 Dapr sidecar |
| **coder-dapr** | - | coder용 Dapr sidecar |
| **redis** | 6381 | Dapr State Store (캐시) + Pub/Sub 브로커 |
| **placement** | 50006 | Dapr Placement 서비스 |

## 데이터 흐름

| 파이프라인 | 경로 | 내용 |
|-----------|------|------|
| **Traces** | Agent → OTel → Tempo → Grafana | span hierarchy, params, 토큰 수, 지연시간 |
| **Logs** | Agent → OTel → Loki → Grafana | LLM 호출, 에러, 비용 로그 |
| **Metrics** | Agent → OTel → Prometheus → Grafana | 실행 횟수, 토큰 사용량, P95 지연 |
| **Cost** | Agent `/stats` API | 모델별 누적 토큰/비용 |

## 요청 흐름 (Trace 구조)

### 기본 모드
```
[orchestrator] POST /run
  └─ agent-run (param.user_id=..., param.session_id=...)
       ├─ llm-call (gpt-4.1, 라우팅 결정)
       ├─ sub-agent-call → [search] POST /run
       │                      └─ agent-run (params 전파됨)
       │                           └─ llm-call (gpt-4.1-mini)
       └─ llm-call (gpt-4.1, 최종 종합)
```

### Dapr 모드
```
[orchestrator] POST /run
  └─ agent-run
       ├─ llm-call (gpt-4.1, 라우팅 결정)
       ├─ sub-agent-call (via_dapr=true)
       │    → localhost:3500/v1.0/invoke/agent-search/method/run
       │         └─ [search-dapr] → [search] /run
       │              ├─ cache check (Dapr State Store → Redis)
       │              ├─ llm-call (gpt-4.1-mini)
       │              └─ cache save (Dapr State Store → Redis)
       ├─ llm-call (gpt-4.1, 최종 종합)
       └─ publish event (Dapr Pub/Sub → Redis → 모든 agent)
```

W3C Trace Context가 `HTTPXClientInstrumentor`에 의해 자동 전파되어, orchestrator → sub-agent 호출이 하나의 trace로 연결된다.

## 프로젝트 구조

```
lgtm/
├── docker-compose.yml              # 기본 모드 (직접 HTTP 통신)
├── docker-compose.dapr.yml         # Dapr 모드 (sidecar 기반)
├── .env                            # AOAI 인증 정보 (git 미포함)
│
├── dapr-components/                # Dapr 컴포넌트 설정 (플랫폼 관리)
│   ├── statestore.yaml             #   Redis State Store (캐시)
│   ├── pubsub.yaml                 #   Redis Pub/Sub (이벤트)
│   ├── secretstore.yaml            #   Secret Store (시크릿 관리)
│   ├── resiliency.yaml             #   Retry / Timeout / Circuit Breaker
│   └── dapr-config.yaml            #   mDNS resolver + Access Control
│
├── dapr-secrets/                   # 시크릿 파일 (git 미포함)
│   └── secrets.json                #   로컬 시크릿 (개발용)
│
├── agent/                          # 멀티에이전트 코드 (공유 모듈)
│   ├── app.py                      #   FastAPI + AOAI + OTel + Dapr
│   ├── config.py                   #   에이전트 프로필 + Dapr 설정
│   ├── llm.py                      #   AOAI 호출 + Secret Store + Service Invocation
│   ├── cache.py                    #   인메모리 / Dapr State Store 듀얼 모드
│   ├── models.py                   #   요청/응답 Pydantic 모델
│   ├── stats.py                    #   비용 추적 + 쿼터 관리
│   ├── otel_setup.py               #   OTel SDK 초기화
│   ├── requirements.txt
│   └── Dockerfile
│
├── agent-template/                 # 새 Agent 생성 템플릿 (Cookiecutter)
│   ├── cookiecutter.json           #   agent_name, model, port 입력
│   ├── hooks/post_gen_project.sh   #   플랫폼 모듈 자동 복사 + 가이드 출력
│   └── {{cookiecutter.agent_name}}/
│       ├── app.py                  #   비즈니스 로직 템플릿
│       ├── Dockerfile
│       └── requirements.txt
│
├── helm/                           # K8s 프로덕션 배포 (Helm Chart)
│   └── agent-platform/
│       ├── Chart.yaml
│       ├── values.yaml             #   Agent 목록 + 플랫폼 설정
│       └── templates/
│           ├── agent-deployment.yaml
│           ├── agent-service.yaml
│           ├── dapr-components.yaml
│           ├── dapr-config.yaml
│           ├── dapr-resiliency.yaml
│           └── ingress.yaml
│
├── grafana/                        # Grafana 대시보드 (자동 프로비저닝)
│   └── provisioning/
│       ├── datasources/
│       └── dashboards/json/
│           ├── multi-agent-overview.json   # Agent 전체 현황
│           ├── cost-tracker.json           # 비용 추적
│           └── dapr-health.json            # Dapr 플랫폼 상태
│
├── yamls/                          # Observability 설정
│   ├── otel-config.yaml
│   ├── prometheus.yml
│   └── tempo.yaml
├── Makefile
├── loadgen/
├── tests/
└── docs/
```

## 플랫폼 제공 기능

| 기능 | 설명 | Agent 개발자 액션 |
|------|------|-------------------|
| **Service Invocation** | Dapr sidecar 경유 서비스 호출 | `localhost:3500` 호출만 |
| **State Store** | Redis 분산 캐시 (자동 TTL) | 코드 변경 없음 (자동 적용) |
| **Pub/Sub** | Agent 완료 이벤트 브로드캐스트 | 코드 변경 없음 (자동 적용) |
| **Secret Store** | AOAI 키를 Dapr Secret Store에서 조회 | `.env` 직접 접근 불가 |
| **Resiliency** | Retry 3회, Timeout 30s, Circuit Breaker | 코드 변경 없음 (Dapr 처리) |
| **Access Control** | Orchestrator만 sub-agent 호출 가능 | 코드 변경 없음 (Dapr 정책) |
| **Observability** | Traces/Metrics/Logs 자동 수집 + 대시보드 | 코드 변경 없음 (OTel 자동) |
| **Agent Template** | Cookiecutter로 새 agent 생성 | `cookiecutter agent-template` |
| **Helm Chart** | K8s 배포 시 values.yaml만 수정 | agent 항목 추가만 |

## 빠른 시작

### 기본 모드

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

### Dapr 모드

```bash
# 1. 시크릿 설정
#    개발: dapr-secrets/secrets.json 에 AOAI 키 입력
#    운영: Azure Key Vault 연동 (dapr-components/secretstore.yaml 변경)
vi dapr-secrets/secrets.json

# 2. Dapr 모드로 실행 (Redis + Placement + Sidecar 포함)
make dapr-up

# 3. 헬스체크
make health

# 4. 테스트 (동일한 API)
make test-orchestrator

# 5. Dapr sidecar 로그 확인
make dapr-logs

# 6. 중지
make dapr-down
```

### 새 Agent 온보딩 (Agent 개발자용)

```bash
# 1. 템플릿으로 agent 생성
pip install cookiecutter
cookiecutter agent-template \
  --no-input \
  agent_name=reviewer \
  agent_type=reviewer \
  model=gpt-4.1-mini \
  port=8004 \
  system_prompt="You are a code reviewer. Review code for bugs and best practices."

# 2. 비즈니스 로직 수정
vi reviewer/app.py    # '비즈니스 로직' 섹션만 수정

# 3. docker-compose.dapr.yml 에 서비스 추가 (post_gen 스크립트가 안내)
# 4. 실행
make dapr-up
```

### K8s 프로덕션 배포

```bash
# 1. values.yaml 에 agent 추가
vi helm/agent-platform/values.yaml

# 2. Helm 배포
helm install agent-platform ./helm/agent-platform \
  --namespace ai-agents --create-namespace

# 3. 새 agent 추가 시
helm upgrade agent-platform ./helm/agent-platform
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

### 공통

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `AZURE_OPENAI_API_KEY` | - | AOAI API 키 |
| `AZURE_OPENAI_ENDPOINT` | - | AOAI 엔드포인트 |
| `AZURE_OPENAI_API_VERSION` | `2024-12-01-preview` | API 버전 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTel Collector gRPC |
| `OTEL_SERVICE_NAME` | `ai-agent` | 서비스 식별명 |
| `AGENT_TYPE` | `default` | `orchestrator` / `search` / `summarizer` / `coder` |

### Dapr 모드 전용

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `USE_DAPR` | `false` | `true`로 설정 시 Dapr 기능 활성화 |
| `DAPR_HTTP_PORT` | `3500` | Dapr sidecar HTTP 포트 |

## 수집되는 텔레메트리

### Traces (Tempo)

| Span | Attributes |
|------|-----------|
| `agent-run` | `agent.type`, `request.query`, `param.*` (커스텀 파라미터) |
| `llm-call` | `llm.model`, `llm.prompt_tokens`, `llm.completion_tokens`, `llm.total_tokens`, `llm.duration` |
| `sub-agent-call` | `sub_agent.name`, `sub_agent.url`, `sub_agent.status`, `sub_agent.via_dapr` |

### Metrics (Prometheus)

| 메트릭 | 타입 | Labels |
|--------|------|--------|
| `agent.run.count` | Counter | `agent.type` |
| `agent.error.count` | Counter | `agent.type`, `error.type` |
| `llm.call.duration` | Histogram | `agent.type`, `llm.model` |
| `llm.token.usage` | Counter | `llm.model`, `type` (prompt/completion) |
| `llm.cost.usd` | Counter | `llm.model`, `agent.type` |
| `llm.tokens.per_request` | Histogram | `llm.model`, `agent.type` |
| `llm.rate_limit.count` | Counter | `llm.model`, `agent.type` |
| `llm.retry.count` | Counter | `llm.model`, `reason` |
| `cache.hit.count` | Counter | `agent.type` |
| `cache.miss.count` | Counter | `agent.type` |
| `quota.reject.count` | Counter | `agent.type` |

### Logs (Loki)

| 메시지 | 레벨 | 포함 정보 |
|--------|------|----------|
| `Agent run started` | INFO | agent_type, query, params |
| `LLM call completed` | INFO | model, tokens, duration, cost_usd, retries |
| `Sub-agent call completed/failed` | INFO/ERROR | tool, status, via_dapr, error |
| `Cache hit/miss` | INFO | agent_type, query |
| `Rate limit 429` | WARNING | model, retry_after |
| `Quota exceeded` | WARNING | agent_type, reason |
| `Dapr event received` | INFO | event_type, source_agent (Dapr 모드) |
| `AOAI credentials loaded from Dapr Secret Store` | INFO | (Dapr 모드) |

## Grafana 대시보드

Grafana 접속: http://localhost:3000 (인증 불필요, Anonymous Admin)

### 프리셋 대시보드 (자동 프로비저닝)

| 대시보드 | 내용 |
|----------|------|
| **Multi-Agent Overview** | Agent별 RPS, 에러율, P95 지연시간, 토큰 사용량 |
| **Cost Tracker** | 총 비용, 분당 비용, 모델별/Agent별 비용 비율, 캐시 히트율, Rate Limit 현황 |
| **Dapr Platform Health** | 서비스 성공률, 활성 Agent 수, P50/P95/P99 지연, 에러율, 로그, 트레이스 탐색 |

### 수동 쿼리

#### Traces (Tempo)

```
# 특정 사용자의 트레이스
{ span.param.user_id = "user-123" }

# 느린 LLM 호출
{ name = "llm-call" && duration > 2s }

# 특정 모델
{ span.llm.model = "gpt-4.1" }

# 에러 트레이스
{ status = error }

# Dapr 경유 호출 (Dapr 모드)
{ span.sub_agent.via_dapr = "true" }
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

### 기본 모드

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

### Dapr 모드

| 명령어 | 설명 |
|--------|------|
| `make dapr-up` | Dapr 모드 스택 빌드 + 실행 |
| `make dapr-down` | Dapr 모드 전체 중지 |
| `make dapr-restart` | Dapr 모드 재시작 |
| `make dapr-status` | Dapr 모드 서비스 상태 (sidecar 포함) |
| `make dapr-logs` | Dapr sidecar 로그 |

## Dapr 컴포넌트 상세 (플랫폼 엔지니어용)

### Resiliency (`dapr-components/resiliency.yaml`)

| 정책 | 대상 | 설정 |
|------|------|------|
| **agentRetry** | sub-agent 호출 | constant 3s, 최대 3회 |
| **stateRetry** | state store / pub/sub | exponential, 최대 10s, 5회 |
| **agentTimeout** | sub-agent 호출 | 30s |
| **stateTimeout** | state store / pub/sub | 5s |
| **agentCB** | sub-agent 호출 | 연속 3회 실패 시 circuit open, 30s 후 half-open |

### Access Control (`dapr-components/dapr-config.yaml`)

| App ID | 허용 | 차단 |
|--------|------|------|
| **agent-orchestrator** | 모든 endpoint | - |
| **agent-search** | `/run`, `/events`, `/health` | 그 외 전부 |
| **agent-summarizer** | `/run`, `/events`, `/health` | 그 외 전부 |
| **agent-coder** | `/run`, `/events`, `/health` | 그 외 전부 |

sub-agent끼리는 직접 호출할 수 없다. 반드시 orchestrator를 경유해야 한다.

### Secret Store (`dapr-components/secretstore.yaml`)

| 환경 | 타입 | 설정 |
|------|------|------|
| **개발** | `secretstores.local.file` | `dapr-secrets/secrets.json` 참조 |
| **운영** | `secretstores.azure.keyvault` | Azure Key Vault 연동 (Helm Chart에 포함) |

Agent 앱은 `localhost:3500/v1.0/secrets/secret-store/azure-openai`로 시크릿을 조회한다. `.env` 파일을 직접 읽지 않으므로 API 키가 Agent 컨테이너에 노출되지 않는다.

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
