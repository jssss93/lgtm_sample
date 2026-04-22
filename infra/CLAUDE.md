# infra/ — 인프라 영역 (배포/관측 설정 통합)

3가지 인프라 자산을 하위 디렉토리로 분리한다.

```
infra/
├── helm/       Kubernetes Helm Chart (프로덕션 + 로컬 K8s)
├── k8s/        로컬 K8s 원시 매니페스트 (모니터링, Langfuse 등)
└── grafana/    Grafana 프로비저닝
```

## 배포 명령어

```bash
# K8s (원스텝)
make k8s-up              # 원스텝 배포 (이미지 빌드 + 모니터링 + Agent + Langfuse)
make k8s-clean           # 전체 정리
```

## Helm values 계층

```
values.yaml               ← 프로덕션 기본값
  └─ values-local-base.yaml   ← 로컬 공통 (agents, image, security)
       ├─ values-local.yaml        ← dapr.enabled: false
       └─ values-local-dapr.yaml   ← dapr.enabled: true + Redis + Access Control
```

`helm install ... -f values-local-base.yaml -f values-local.yaml` 형태로 사용.
