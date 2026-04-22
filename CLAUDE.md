# CLAUDE.md — LGTM Stack Project Guide

## Project Overview

Azure OpenAI 기반 멀티에이전트 시스템 + LGTM 관측성 스택.
Kubernetes(Helm) 단일 배포 모드.

## Directory Map

프로젝트는 **개발 영역(`apps/`)** 과 **인프라 영역(`infra/`)** 으로 분리됨.

```
lgtm/
├── apps/                    # 개발 영역 (애플리케이션 코드)
│   ├── agent/               # 멀티에이전트 플랫폼 (Clean Architecture)
│   ├── agent-template/      # Cookiecutter 스캐폴딩
│   └── loadgen/             # 부하 생성기
├── infra/                   # 인프라 영역 (배포/관찰 설정)
│   ├── helm/                # Kubernetes Helm Chart
│   ├── k8s/                 # K8s 원시 매니페스트 (모니터링, langfuse 등)
│   └── grafana/             # Grafana 프로비저닝
├── tests/                   # 테스트 (앱·인프라 황단)
├── docs/                    # 설계 문서
└── Makefile                 # 전체 워크플로우
```

### `apps/agent/` — 에이전트 앱 코드 (Python FastAPI, Clean Architecture)

**도메인 레이어** — 외부 의존 없는 순수 정책
- `domain/ports.py` — `CacheBackend`, `LLMProvider`, `MetricsRecorder`, `EventPublisher`, `PromptProvider` 인터페이스(ABC)
- `domain/value_objects.py` — `LLMTokens`, `CachedResult`, `UserQuota` (불변 값 객체)
- `domain/prompt.py` — `PromptResolution` 불변 값 객체 (system_prompt, version, variant, source, content_hash)
- `domain/quality.py` — `compute_quality_score(text)` 순수 함수. 길이·에러 키워드·문장 완결성 기반 0.0~1.0 점수 반환

**애플리케이션 레이어** — 비즈니스 흐름 조율, 포트만 의존
- `application/use_cases.py` — `SubAgentUseCase` (프롬프트 해석→캐시→LLM→저장), `OrchestratorUseCase` (routing→병렬 호출→합성). 프롬프트 버전별 캐시 격리 + quality metric emit

**인프라 레이어** — 포트 구현체, 외부 서비스 연결
- `infrastructure/cache_memory.py` — `MemoryCacheBackend` (OrderedDict LRU + TTL)
- `infrastructure/cache_dapr.py` — `DaprCacheBackend` (Redis via Dapr State Store)
- `infrastructure/llm_aoai.py` — `AzureOpenAIProvider` (Circuit Breaker + Exponential Backoff)
- `infrastructure/metrics_otel.py` — `OTelMetricsRecorder`, `NoOpMetricsRecorder` (테스트용). `record_quality_score`, `record_prompt_reload`, `record_prompt_selection` 포함
- `infrastructure/events.py` — `DaprEventPublisher`, `NoOpEventPublisher`
- `infrastructure/sub_agent_invoker.py` — `SubAgentInvoker` (HTTP 직접 / Dapr Service Invocation)
- `infrastructure/prompt_manager.py` — `YamlPromptManager` (YAML 로딩, 버전 관리, A/B Sticky Assignment, watchfiles Hot-Reload)

**조립 & HTTP 레이어**
- `container.py` — DI 조립. `build_use_case()` 한 번 호출로 모든 의존성 주입
- `app.py` — FastAPI thin layer. 엔드포인트 (/run, /health, /stats, /cache/clear, /events, /prompts) + 예외 → HTTP 변환만 담당

**하위호환 퍼사드** (기존 import 유지)
- `cache.py` — `cache_get/set/clear/size` → 백엔드에 위임
- `llm.py` — `call_aoai`, `execute_tool_call` → `AzureOpenAIProvider`에 위임

**프롬프트 관리** (PromptOps)
- `prompts/orchestrator.yaml` — 오케스트레이터 프롬프트 (버전 관리 + A/B 테스트 설정)
- `prompts/search.yaml` — 검색 에이전트 프롬프트
- `prompts/summarizer.yaml` — 요약 에이전트 프롬프트
- `prompts/coder.yaml` — 코딩 에이전트 프롬프트

**변경 없음**
- `config.py` — 환경변수, 에이전트 프로필 (프롬프트 fallback), 가격표(`PRICING`), Dapr 설정
- `models.py` — `AgentRequest`, `AgentResponse` (prompt_version 필드 포함) Pydantic 스키마
- `stats.py` — 인메모리 비용 추적, 쿼터 관리
- `otel_setup.py` — OTel SDK 초기화. `quality_score_histogram`, `prompt_reload_counter`, `prompt_selection_counter` 포함
- `Dockerfile` — python:3.13-slim, non-root user

### `apps/agent-template/` — 새 Agent 스캐폴딩 (Cookiecutter)
- `cookiecutter.json` — agent_name, model, port 입력 변수
- `hooks/post_gen_project.sh` — 플랫폼 모듈 자동 복사 스크립트

### `infra/` — 배포/운영 설정 통합
#### `infra/helm/` — Kubernetes Helm Chart
- `Chart.yaml` — chart 메타 (agent-platform v0.1.0)
- `values.yaml` — 프로덕션 기본값 (agents 4개, 보안, HA, KEDA 설정)
- `values-local-base.yaml` — 로컬 공통 (agents, image:local, KEDA 활성화)
- `values-local.yaml` — Dapr 없이 (base 위에 오버라이드)
- `values-local-dapr.yaml` — Dapr 포함 (base 위에 오버라이드)
- `templates/` — Deployment, Service, RBAC, NetworkPolicy, PDB, Ingress, HPA, KEDA ScaledObject, Dapr CRDs

#### `infra/k8s/` — 로컬 K8s 인프라 매니페스트
- `namespace.yaml` — agent-platform(dapr.io/enabled 레이블 포함) + monitoring NS
- `aoai-secret.yaml` — AOAI Secret 템플릿
- `redis.yaml` — Dapr 모드용 Redis
- `loadtest-job.yaml` — 부하/장애 테스트 Job + Chaos RBAC
- `metrics-server.yaml` — Docker Desktop용 metrics-server 패치
- `monitoring/` — LGTM 스택 (otel-collector, prometheus, loki, tempo, grafana 각 분리)
- `grafana-plugins/` — Dockerfile + 드릴다운 플러그인 zip 3개 + 추출본 plugins/ (폐쇄망용)

### `infra/grafana/` — Grafana 프로비저닝
- `provisioning/datasources/datasources.yaml` — Prometheus, Loki, Tempo 설정
- `provisioning/dashboards/json/` — 대시보드 6개 (overview, cost, dapr-health, container-resources, llm-observability, prompt-ops)
- `provisioning/alerting/alerts.yaml` — 알림 규칙

#### `grafana/provisioning/dashboards/json/llm-observability.json`
LLM Observability 전용 대시보드 (Langfuse 대체). 섹션 구성:
- **KPI**: 총 호출 수, 비용, 토큰, P95 레이턴시, 에러율, 캐시 히트율
- **Trace Explorer**: Tempo TraceQL `{ name = "llm-call" }` 기반 프롬프트/응답 드릴다운
- **Token & Cost**: 모델별 토큰/비용 추이, 누적 비용 파이차트
- **Latency Distribution**: P50/P90/P99 히스토그램, RPS
- **Error & Resilience**: Rate Limit/Retry/Circuit Breaker 시계열 + 에러 로그 + 쿼터/캐시
- **LLM Quality Evaluation** *(신규)*: 품질 점수 평균·분위수·저품질 비율 + Loki 품질 이슈 로그

#### `grafana/provisioning/dashboards/json/prompt-ops.json`
Prompt Operations 대시보드. 섹션 구성:
- **Prompt Status**: 총 선택 수, 리로드 수, 에이전트별 활성 버전 테이블
- **Version Distribution**: 버전별 선택 비율 파이차트, 시간대별 선택 RPS, A/B 변이 분포
- **Quality by Prompt Version**: 버전별 평균 품질 점수, 품질 분포 히스토그램, Judge 점수 비교, 저품질 비율
- **Prompt Reload Events**: 리로드 타임라인, 리로드 로그 (Loki), 누적 리로드 수, 버전 변경 로그

### `tests/` — 테스트
- `test_unit.py` — 단위 테스트 39개 (도메인·인프라 계층 직접 검증, PromptOps 10개 포함)
- `test_agents.py` — 에이전트 통합 테스트
- `test_helm_values.sh` — Helm 차트 검증 (18 checks, 클러스터 불필요)
- `test_k8s_smoke.sh` — K8s E2E 스모크 테스트 (29 checks)

### `apps/loadgen/` — 부하 생성기
- `run.py` — 15개 질의, 30% Heavy 워크로드 자동 생성

### `docs/` — 설계 문서
- `monitoring-design.md` — 모니터링 아키텍처 설계 (14개 섹션)
- `observability-guide.md` — Grafana 조회 가이드 (TraceQL, PromQL, LogQL)

## Key Commands

```bash
# K8s 배포
make k8s-up              # 원스텝 배포 (이미지 빌드 + 모니터링 + Agent + Langfuse)
make k8s-clean           # 전체 정리

# 테스트
make test-unit           # Python 단위 테스트
make test-helm           # Helm 차트 검증
make test-k8s            # K8s E2E 스모크 테스트
```

## Tech Stack

- **App**: Python 3.12, FastAPI, Azure OpenAI (gpt-4.1 / gpt-4.1-mini)
- **Architecture**: Hexagonal (Ports & Adapters) — domain / application / infrastructure / container 4-layer
- **Observability**: OpenTelemetry SDK → OTel Collector → Prometheus + Loki + Tempo → Grafana 12.4.2
- **Dapr**: 1.17.3 (Service Invocation, State Store, Pub/Sub, Secret Store, Resiliency)
- **K8s**: Helm Chart + RBAC + SecurityContext + PDB + NetworkPolicy + KEDA (Prometheus 기반 오토스케일)
- **AutoScaling**: KEDA 2.x — agent별 ScaledObject, Prometheus `agent_run_count_total` RPS 트리거, minReplicas=1

## Conventions

- Helm values 계층: `values.yaml` ← `values-local-base.yaml` ← `values-local[-dapr].yaml`
- K8s 모니터링 배포 순서: 백엔드(Tempo/Loki/Prometheus) Ready → OTel Collector restart → Grafana
- 에이전트 이미지: `agent:local`, Grafana 이미지: `grafana-custom:local` (플러그인 baked-in)
- hostPath 볼륨: `../lgtm_volume/` (prometheus, loki, tempo, grafana)
- 새 에이전트 추가 시: `container.py`의 `build_use_case()`에 분기 추가 또는 `apps/agent-template/` Cookiecutter 사용
- **품질 점수 metric**: `MetricsRecorder` 포트에 새 메서드 추가 시 `OTelMetricsRecorder` + `NoOpMetricsRecorder` 양쪽 모두 구현 필수
- **품질 점수 계산**: `domain.quality.compute_quality_score()` — 길이·에러 키워드·문장 완결성 기반 휴리스틱 (캐시 히트 경로는 emit 안 함)
- **PromptOps**: `apps/agent/prompts/*.yaml`에 프롬프트 정의. `YamlPromptManager`가 Hot-Reload + A/B 테스트 제공. 캐시 키에 `prompt_version` 포함하여 버전별 격리
- **프롬프트 YAML 스키마**: `versions` (버전별 system_prompt) + `active` (활성 버전) + `ab_test` (enabled, variants[{version, weight}])
- **프롬프트 변경**: YAML 파일 수정 → watchfiles가 자동 감지 → 무중단 Hot-Reload (재기동 불필요)
