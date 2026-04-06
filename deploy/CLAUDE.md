# deploy/ — 배포 설정 통합

3가지 배포 환경의 설정을 하위 디렉토리로 분리한다.

```
deploy/
├── compose/    Docker Compose 전용 (로컬 개발)
├── helm/       Kubernetes Helm Chart (프로덕션 + 로컬 K8s)
└── k8s/        로컬 K8s 인프라 매니페스트 (모니터링 스택 등)
```

## 배포 명령어

```bash
# Docker Compose
make up                  # 기본 모드
make dapr-up             # Dapr 모드

# K8s (원스텝)
make k8s-up              # Dapr 없이
make k8s-up-dapr         # Dapr 포함
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
