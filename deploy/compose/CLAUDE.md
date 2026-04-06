# deploy/compose/ — Docker Compose 전용 설정

프로젝트 루트의 `docker-compose.yml`과 `docker-compose.dapr.yml`에서 볼륨으로 마운트되는 설정 파일들.

## 구조

```
compose/
├── configs/              OTel/Prometheus/Tempo 설정
│   ├── otel-config.yaml    OTel Collector 파이프라인 (receivers → processors → exporters)
│   ├── prometheus.yml      Prometheus scrape 설정 (OTel Collector :8889)
│   └── tempo.yaml          Tempo 설정 (로컬 스토리지, metrics_generator → Prometheus remote write)
└── dapr/                 Dapr 컴포넌트 + 시크릿
    ├── components/         statestore(Redis), pubsub(Redis), secretstore(local file), resiliency, dapr-config
    └── secrets/            secrets.json (AOAI 키, 로컬 개발용)
```

## docker-compose에서 마운트 경로

```yaml
# docker-compose.yml
volumes:
  - ./deploy/compose/configs/prometheus.yml:/etc/prometheus/prometheus.yml
  - ./deploy/compose/configs/tempo.yaml:/etc/tempo.yaml
  - ./deploy/compose/configs/otel-config.yaml:/etc/otel-config.yaml

# docker-compose.dapr.yml (Dapr sidecar)
volumes:
  - ./deploy/compose/dapr/components:/components
  - ./deploy/compose/dapr/components/dapr-config.yaml:/config/dapr-config.yaml
  - ./deploy/compose/dapr/secrets:/dapr-secrets:ro
```

## configs/ 주요 설정

| 파일 | 핵심 설정 |
|------|----------|
| `otel-config.yaml` | receivers: otlp(gRPC 4317) + docker_stats. exporters: prometheus(:8889), otlphttp/loki, otlp/tempo. batch processor: 1024건/5s |
| `prometheus.yml` | scrape_interval: 5s, targets: otel-collector:8889 |
| `tempo.yaml` | metrics_generator: span-metrics + service-graphs → Prometheus remote write. storage: local file |

## dapr/components/ Resiliency 정책

| 정책 | 대상 | 설정 |
|------|------|------|
| agentRetry | sub-agent 호출 | constant 3s, max 3회 |
| stateRetry | state store / pub/sub | exponential, max 10s, 5회 |
| agentTimeout | sub-agent 호출 | 30s |
| agentCB | sub-agent 호출 | 연속 3회 실패 → circuit open, 30s 후 half-open |
