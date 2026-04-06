# deploy/k8s/ — 로컬 K8s 인프라 매니페스트

Helm Chart에 포함되지 않는 인프라 리소스. `kubectl apply -f` 또는 Makefile로 배포.

## 파일

| 파일 | 역할 |
|------|------|
| `namespace.yaml` | `agent-platform` + `monitoring` 네임스페이스 생성 |
| `aoai-secret.yaml` | AOAI Secret 템플릿 (실제 값은 `.env`에서 `kubectl create secret`으로 생성) |
| `redis.yaml` | Redis Deployment + Service (Dapr State Store/PubSub 백엔드, port 6379) |
| `loadtest-job.yaml` | 부하 테스트 Job + 장애 복구 테스트 Job + chaos-test-sa RBAC |
| `metrics-server.yaml` | Docker Desktop용 metrics-server (--kubelet-insecure-tls, --metric-resolution=30s) |

## monitoring/ — LGTM 관측성 스택

| 파일 | 컴포넌트 | 핵심 설정 |
|------|----------|----------|
| `otel-collector.yaml` | OTel Collector | retry_on_failure(5~30s), sending_queue(100건). exporters: tempo:4317, loki:3100, prometheus:8889 |
| `prometheus.yaml` | Prometheus + RBAC | ServiceAccount + ClusterRole. scrape: otel-collector, kubelet-cadvisor, kubelet-resource. hostPath 영속화 |
| `loki.yaml` | Loki | TSDB schema v13, inmemory ring. hostPath 영속화 |
| `tempo.yaml` | Tempo | metrics_generator(span-metrics, service-graphs → Prometheus remote write). memory limit 1Gi. hostPath 영속화 |
| `grafana.yaml` | Grafana | 커스텀 이미지(`grafana-custom:local`, 플러그인 baked-in). NodePort 30300. datasource uid 고정(prometheus, loki, tempo). hostPath 영속화 |

## grafana-plugins/ — Grafana 드릴다운 플러그인

| 파일 | 역할 |
|------|------|
| `Dockerfile` | Grafana 12.4.2 기반. `plugins/` 디렉토리를 직접 COPY하여 설치 |
| `grafana-exploretraces-app.zip` | Traces Drilldown 원본 zip (4.6MB) |
| `grafana-lokiexplore-app.zip` | Logs Drilldown 원본 zip (8.7MB) |
| `grafana-metricsdrilldown-app.zip` | Metrics Drilldown 원본 zip (4.0MB) |
| `plugins/` | zip 추출본 3개 (git 관리, Docker COPY 대상) |

빌드: `docker build -t grafana-custom:local ./deploy/k8s/grafana-plugins`

> `GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS`에 3개 플러그인 명시됨 (`grafana.yaml`)

## hostPath 볼륨 (영속화)

| 컴포넌트 | 경로 | UID |
|----------|------|-----|
| Prometheus | `lgtm_volume/prometheus` | 65534 |
| Loki | `lgtm_volume/loki` | 10001 |
| Tempo | `lgtm_volume/tempo` | 10001 |
| Grafana | `lgtm_volume/grafana` | 472 |

각 Deployment에 `initContainer(runAsUser: 0)`로 `chown` 실행.

## 배포 순서 (Makefile k8s-monitoring)

1. `kubectl apply -f deploy/k8s/monitoring/` (전체 적용)
2. Tempo, Loki, Prometheus Ready 대기
3. OTel Collector `rollout restart` (DNS 캐시 방지)
4. OTel Collector Ready 대기
5. Grafana Ready 대기
