# grafana/ — Grafana 프로비저닝 (Compose + K8s 공용)

Grafana 시작 시 자동 로드되는 datasource, dashboard, alert 설정.
Docker Compose에서는 볼륨 마운트, K8s에서는 ConfigMap으로 주입.

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
        └── container-resources.json    Pod CPU/메모리 (K8s, pod_cpu_usage_seconds_total)
```

## 대시보드 datasource UID

K8s 모드에서 datasource uid가 대시보드 JSON의 참조와 일치해야 한다:
- Prometheus: `uid: prometheus`
- Loki: `uid: loki`
- Tempo: `uid: tempo`

`deploy/k8s/monitoring/grafana.yaml`의 datasources ConfigMap에서 고정.

## K8s에서 대시보드 배포 방식

`grafana/provisioning/dashboards/json/` → `kubectl create configmap grafana-dashboards --from-file=...` → Grafana Pod의 `/etc/grafana/provisioning/dashboards/json/`에 마운트.
