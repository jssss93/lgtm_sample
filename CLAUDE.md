# CLAUDE.md — LGTM Stack Project Guide

## Project Overview

Azure OpenAI 기반 멀티에이전트 시스템 + LGTM 관측성 스택.
3가지 배포 모드: Docker Compose(기본/Dapr) + Kubernetes(Helm).

## Directory Map

### `agent/` — 에이전트 앱 코드 (Python FastAPI)
- `app.py` — FastAPI 엔드포인트 (/run, /health, /stats, /events)
- `config.py` — 에이전트 프로필, 모델 설정, Dapr 설정, 가격표
- `llm.py` — Azure OpenAI 호출, sub-agent 호출 (HTTP/Dapr), 재시도 로직
- `cache.py` — 인메모리 LRU / Dapr State Store 듀얼 모드 캐시
- `otel_setup.py` — OpenTelemetry SDK 초기화 (traces, metrics, logs)
- `models.py` — AgentRequest/AgentResponse Pydantic 모델
- `stats.py` — 비용 추적, 쿼터 관리
- `Dockerfile` — python:3.12-slim, non-root user

### `agent-template/` — 새 Agent 스캐폴딩 (Cookiecutter)
- `cookiecutter.json` — agent_name, model, port 입력 변수
- `hooks/post_gen_project.sh` — 플랫폼 모듈 자동 복사 스크립트

### `deploy/` — 배포 설정 통합
#### `deploy/compose/` — Docker Compose 전용
- `configs/` — otel-config.yaml, prometheus.yml, tempo.yaml
- `dapr/components/` — statestore, pubsub, secretstore, resiliency, dapr-config
- `dapr/secrets/` — secrets.json (로컬 개발용)

#### `deploy/helm/` — Kubernetes Helm Chart
- `Chart.yaml` — chart 메타 (agent-platform v0.1.0)
- `values.yaml` — 프로덕션 기본값 (agents 4개, 보안, HA 설정)
- `values-local-base.yaml` — 로컬 공통 (agents, image:local, security)
- `values-local.yaml` — Dapr 없이 (base 위에 오버라이드)
- `values-local-dapr.yaml` — Dapr 포함 (base 위에 오버라이드)
- `templates/` — Deployment, Service, RBAC, NetworkPolicy, PDB, Ingress, Dapr CRDs

#### `deploy/k8s/` — 로컬 K8s 인프라 매니페스트
- `namespace.yaml` — agent-platform + monitoring NS
- `aoai-secret.yaml` — AOAI Secret 템플릿
- `redis.yaml` — Dapr 모드용 Redis
- `loadtest-job.yaml` — 부하/장애 테스트 Job + Chaos RBAC
- `metrics-server.yaml` — Docker Desktop용 metrics-server 패치
- `monitoring/` — LGTM 스택 (otel-collector, prometheus, loki, tempo, grafana 각 분리)
- `grafana-plugins/` — Dockerfile + 드릴다운 플러그인 zip 3개 (폐쇄망용)

### `grafana/` — Grafana 프로비저닝 (Compose + K8s 공용)
- `provisioning/datasources/datasources.yaml` — Prometheus, Loki, Tempo 설정
- `provisioning/dashboards/json/` — 대시보드 4개 (overview, cost, dapr-health, container-resources)
- `provisioning/alerting/alerts.yaml` — 알림 규칙

### `tests/` — 테스트
- `test_unit.py` — 단위 테스트 15개
- `test_agents.py` — 에이전트 통합 테스트
- `test_helm_values.sh` — Helm 차트 검증 (18 checks, 클러스터 불필요)
- `test_k8s_smoke.sh` — K8s E2E 스모크 테스트 (29 checks)

### `loadgen/` — 부하 생성기
- `run.py` — 15개 질의, 30% Heavy 워크로드 자동 생성

### `docs/` — 설계 문서
- `monitoring-design.md` — 모니터링 아키텍처 설계 (14개 섹션)
- `observability-guide.md` — Grafana 조회 가이드 (TraceQL, PromQL, LogQL)

## Key Commands

```bash
# Docker Compose
make up                  # 기본 모드 실행
make dapr-up             # Dapr 모드 실행

# K8s
make k8s-up              # 원스텝 배포 (Dapr 없이)
make k8s-up-dapr         # 원스텝 배포 (Dapr 포함)
make k8s-clean           # 전체 정리

# 테스트
make test-unit           # Python 단위 테스트
make test-helm           # Helm 차트 검증
make test-k8s            # K8s E2E 스모크 테스트
```

## Tech Stack

- **App**: Python 3.12, FastAPI, Azure OpenAI (gpt-4.1 / gpt-4.1-mini)
- **Observability**: OpenTelemetry SDK → OTel Collector → Prometheus + Loki + Tempo → Grafana 12.4.2
- **Dapr**: 1.17.3 (Service Invocation, State Store, Pub/Sub, Secret Store, Resiliency)
- **K8s**: Helm Chart + RBAC + SecurityContext + PDB + NetworkPolicy

## Conventions

- Helm values 계층: `values.yaml` ← `values-local-base.yaml` ← `values-local[-dapr].yaml`
- K8s 모니터링 배포 순서: 백엔드(Tempo/Loki/Prometheus) Ready → OTel Collector restart → Grafana
- 에이전트 이미지: `agent:local`, Grafana 이미지: `grafana-custom:local` (플러그인 baked-in)
- hostPath 볼륨: `../lgtm_volume/` (prometheus, loki, tempo, grafana)
