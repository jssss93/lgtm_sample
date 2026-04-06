# deploy/helm/ — Kubernetes Helm Chart

agent-platform Helm Chart. 에이전트 4개 + Dapr + 보안/HA + KEDA 오토스케일 설정.

## 핵심 파일

| 파일 | 역할 |
|------|------|
| `values.yaml` | 프로덕션 기본값. agents 배열, image, Dapr, Redis, AOAI, OTel, resiliency, accessControl, securityContext, networkPolicy, PDB, affinity, KEDA |
| `values-local-base.yaml` | 로컬 공통. 축소된 리소스(128~256Mi), image: agent:local, KEDA 활성화 |
| `values-local.yaml` | Dapr 없이 (`dapr.enabled: false`) |
| `values-local-dapr.yaml` | Dapr 포함. Redis, Access Control(trustDomain: public), Resiliency |

## 템플릿

| 템플릿 | 생성 리소스 | 조건 |
|--------|-----------|------|
| `agent-deployment.yaml` | Deployment × agents 수 | 항상. securityContext, probes, preStop, rolling update, anti-affinity |
| `agent-service.yaml` | Service × agents 수 | 항상. ClusterIP :8000 |
| `rbac.yaml` | ServiceAccount + Role + RoleBinding | 항상. configmaps/secrets get, pods get |
| `networkpolicy.yaml` | NetworkPolicy × agents 수 | `networkPolicy.enabled` |
| `pdb.yaml` | PDB (replicas > 1만) | `pdb.enabled` |
| `hpa.yaml` | HPA × agents 수 | `agent.hpa.enabled` (KEDA 사용 시 비활성화) |
| `keda-scaledobject.yaml` | ScaledObject × agents 수 | `agent.keda.enabled`. Prometheus RPS 트리거, minReplicas=1 |
| `ingress.yaml` | Ingress (orchestrator만) | 항상 |
| `dapr-components.yaml` | Dapr Component (statestore, pubsub, secret-store) | `dapr.enabled` |
| `dapr-config.yaml` | Dapr Configuration (nameResolution, accessControl) | `dapr.enabled` |
| `dapr-resiliency.yaml` | Dapr Resiliency (retry, timeout, circuitBreaker) | `dapr.enabled` |

## 리소스 설정 구조

requests/limits 분리 지정. 에이전트별 기본값:

| 에이전트 | requests (로컬) | limits (로컬) | requests (prod) | limits (prod) |
|----------|----------------|--------------|----------------|--------------|
| orchestrator | 256Mi/100m | 512Mi/500m | 512Mi/250m | 1Gi/1000m |
| search | 128Mi/50m | 256Mi/200m | 256Mi/100m | 512Mi/500m |
| summarizer | 128Mi/50m | 256Mi/200m | 256Mi/100m | 512Mi/500m |
| coder | 256Mi/100m | 512Mi/500m | 512Mi/250m | 1Gi/1000m |

## KEDA 설정

- Prometheus 트리거: `sum(rate(agent_run_count_total{agent_type="..."}[1m]))`
- `keda.prometheusAddress`: Prometheus 주소 (로컬: `http://prometheus.monitoring:9090`)
- scaleUp: 최대 2 Pod/분, scaleDown: 안정화 5분 후 1 Pod/2분
- KEDA 설치 필요: `helm install keda kedacore/keda -n keda --create-namespace`

## SecurityContext (기본 활성화)

```yaml
Pod:   runAsNonRoot: true, runAsUser: 1000, seccompProfile: RuntimeDefault
Container: allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities.drop: ALL
```

## trustDomain 설정

`accessControl.trustDomain`은 Dapr의 실제 trust domain과 일치해야 한다.
기본값: `"public"` (Dapr init 기본값). values.yaml에서 변경 가능.
