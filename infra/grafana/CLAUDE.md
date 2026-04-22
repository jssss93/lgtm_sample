# grafana/ — Grafana 프로비저닝

Grafana 시작 시 자동 로드되는 datasource, dashboard, alert 설정.
K8s에서는 ConfigMap으로 주입.

## 구조

```
grafana/provisioning/
├── datasources/datasources.yaml   데이터소스 3개 (Prometheus, Loki, Tempo)
├── alerting/alerts.yaml           알림 규칙
└── dashboards/
    ├── dashboards.yaml            프로바이더 설정 (json/ 디렉토리 스캔)
    └── json/
        ├── multi-agent-overview.json   Agent RPS, 에러율, P95, 토큰, 비용, 트레이스 목록
        ├── cost-tracker.json           총 비용, 분당 비용, 모델별 비용, 캐시 히트율
        ├── dapr-health.json            서비스 성공률, 지연, 에러, 로그, 트레이스 탐색
        ├── container-resources.json    Pod CPU/메모리 (K8s, pod_cpu_usage_seconds_total)
        └── llm-observability.json      LLM 호출 상세 관측 (품질 평가 섹션 포함)
```

### `llm-observability.json` 섹션 구성

| 섹션 | 패널 | 데이터소스 |
|------|------|-----------|
| KPI | 총 호출/비용/토큰/P95/에러율/캐시히트 stat 6개 | Prometheus |
| Trace Explorer | LLM Call Traces 테이블 (TraceQL: `{ name = "llm-call" }`) | Tempo |
| Token & Cost | 토큰 추이, 분당 비용, 평균 레이턴시, 누적 비용 파이차트 | Prometheus |
| Latency Distribution | P50/P90/P99 시계열, RPS | Prometheus |
| Error & Resilience | Rate Limit/Retry/CB 시계열, 에러 로그, 쿼터 거절, 캐시 성능 | Prometheus + Loki |
| **LLM Quality Evaluation** | 품질 점수 평균(id=51), P50/P90 분위수(id=52), 저품질 비율 stat(id=53), 품질 이슈 로그(id=54) | Prometheus + Loki |

품질 점수 metric: `llm_quality_score` histogram. `agent_type`, `llm_model` 레이블. 에이전트 코드 `application/use_cases._compute_quality_score()`가 계산하여 emit.

## 대시보드 datasource UID

K8s 모드에서 datasource uid가 대시보드 JSON의 참조와 일치해야 한다:
- Prometheus: `uid: prometheus`
- Loki: `uid: loki`
- Tempo: `uid: tempo`

`infra/k8s/monitoring/grafana.yaml`의 datasources ConfigMap에서 고정.

## K8s에서 대시보드 배포 방식

`grafana/provisioning/dashboards/json/` → `kubectl create configmap grafana-dashboards --from-file=...` → Grafana Pod의 `/etc/grafana/provisioning/dashboards/json/`에 마운트.
