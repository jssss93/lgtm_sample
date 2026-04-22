# tests/ — 테스트 스위트

## 파일

| 파일 | 종류 | 의존성 | 실행 방법 |
|------|------|--------|----------|
| `test_unit.py` | 단위 테스트 (29개) | 없음 | `make test-unit` |
| `test_agents.py` | 통합 테스트 | K8s 배포 필요 | `pytest tests/test_agents.py -v` |
| `test_helm_values.sh` | Helm 차트 검증 (18 checks) | helm CLI만 | `make test-helm` |
| `test_k8s_smoke.sh` | K8s E2E 스모크 (29 checks) | K8s 클러스터 필요 | `make test-k8s` |

## test_unit.py 커버리지 (24개)

### 비용 계산 (2개)
- `calc_cost()`: 모델별 가격 계산 (gpt-4.1, gpt-4.1-mini, unknown)
- `calc_cost()`: 제로 토큰 에지 케이스

### 캐시 키 생성 (4개) — `infrastructure.cache_memory._cache_key`
- 동일 입력 → 동일 SHA256 해시 (결정성)
- 대소문자 무시 ("Hello" ≡ "hello")
- 공백 정규화 ("hello" ≡ "  hello  ")
- 모델 변경 → 다른 키

### MemoryCacheBackend (4개) — `infrastructure.cache_memory.MemoryCacheBackend`
- `get/set`: 캐시 저장·조회, 메타데이터 보존, 대소문자 무관 히트
- `max_size`: LRU 방출 정책 (max_size=3 → 4번째 삽입 시 가장 오래된 항목 제거)
- `clear`: 캐시 초기화 + 삭제 개수 반환
- `TTL 만료`: 만료된 항목 자동 제거, 조회 시 None 반환

### 도메인 값 객체 (5개) — `domain.value_objects`
- `LLMTokens.total`: prompt + completion 합계 계산
- `LLMTokens` 불변성: frozen dataclass, 속성 변경 시 예외
- `UserQuota` 무제한: quota=0이면 항상 통과
- `UserQuota` 토큰 초과: used >= quota 시 에러 메시지 반환
- `UserQuota` 비용 초과: used_usd >= quota_usd 시 에러 메시지 반환

### 메트릭 레코더 (1개) — `infrastructure.metrics_otel.NoOpMetricsRecorder`
- 6개 메서드 호출 시 예외 없이 정상 완료 (NoOp 보증, `record_quality_score` 포함)

### 품질 점수 계산 (5개) — `application.use_cases._compute_quality_score`
- 정상 긴 응답 → 0.8 이상
- 짧은 응답 + 에러 키워드 → 0.5 미만
- 에러 키워드 단독 → 0.8 미만
- 미완결 문장 → 0.8 이하
- 빈 문자열 → 0.0 (바닥값 보장)

### HTTP 모델 (3개) — `models`
- `AgentRequest`: 기본 필드 (query만 필수)
- `AgentRequest`: 전체 필드 (context, params, model_override)
- `AgentResponse`: 직렬화, cached=False/retries=0 기본값

### 쿼터 (1개) — `stats.check_quota`
- params=None 또는 빈 dict → None 반환

### 설정 (2개) — `config`
- AGENT_PROFILES: orchestrator/search/summarizer/coder 4개 존재, deployment+system_prompt 필드
- PRICING: 모든 모델에 prompt/completion 가격 정의 (≥0)

### 포트 & 어댑터 계약 (2개) — `domain.ports`, `infrastructure.cache_memory`
- `CacheBackend` 추상 클래스 직접 인스턴스화 시 TypeError
- `MemoryCacheBackend`가 `CacheBackend` isinstance 통과

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
