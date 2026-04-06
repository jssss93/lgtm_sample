# tests/ — 테스트 스위트

## 파일

| 파일 | 종류 | 의존성 | 실행 방법 |
|------|------|--------|----------|
| `test_unit.py` | 단위 테스트 (15개) | 없음 | `make test-unit` |
| `test_agents.py` | 통합 테스트 | docker-compose up 필요 | `pytest tests/test_agents.py -v` |
| `test_helm_values.sh` | Helm 차트 검증 (18 checks) | helm CLI만 | `make test-helm` |
| `test_k8s_smoke.sh` | K8s E2E 스모크 (29 checks) | K8s 클러스터 필요 | `make test-k8s` |

## test_unit.py 커버리지

- `calc_cost()`: 모델별 가격 계산
- 캐시 키: 대소문자 무시, 공백 정규화, SHA256 결정성
- 캐시 get/set: TTL, max size, LRU 방출
- 쿼터: 토큰/비용 일일 제한 체크
- 모델: AgentRequest/AgentResponse 직렬화
- config: 프로필 존재, 가격표 존재

## test_helm_values.sh 체크 항목 (18개)

- helm lint: default / local / dapr values
- helm template: 렌더링 성공
- 에이전트 4개 존재 (orchestrator, search, summarizer, coder)
- Dapr annotation: dapr 모드에만 존재
- RBAC: ServiceAccount, Role, RoleBinding
- SecurityContext: runAsNonRoot, allowPrivilegeEscalation
- trustDomain: public
- preStop hook 존재

## test_k8s_smoke.sh 체크 항목 (29개)

- 네임스페이스 2개
- 모니터링 Pod 5개 Ready
- 에이전트 Pod 4개 Ready
- Dapr sidecar 주입 여부
- Service 4개 존재
- ConfigMap (grafana-dashboards, 4개 대시보드)
- RBAC (configmaps get 허용, deployments create 거부)
- SecurityContext (runAsNonRoot=true)
- Grafana API healthy + datasource 3개 + plugin 3개 enabled
- orchestrator 쿼리 HTTP 200
- Prometheus 메트릭 수집 확인
- Tempo 트레이스 수집 확인
