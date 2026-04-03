#!/bin/bash
# ════════════════════════════════════════════════════════════════
# K8s E2E 스모크 테스트 (클러스터 필요)
# Usage: ./tests/test_k8s_smoke.sh
# ════════════════════════════════════════════════════════════════
set -o pipefail
cd "$(dirname "$0")/.."

K8S_NS="agent-platform"
MON_NS="monitoring"
PASS=0; FAIL=0

pass() { echo "  ✅ $1"; PASS=$((PASS+1)); }
fail() { echo "  ❌ $1"; FAIL=$((FAIL+1)); }

echo "=== K8s 스모크 테스트 ==="

# ──────────────────────────── 1. 네임스페이스 ──────
echo ""
echo "--- 네임스페이스 ---"
kubectl get ns $K8S_NS > /dev/null 2>&1 && pass "namespace $K8S_NS" || fail "namespace $K8S_NS"
kubectl get ns $MON_NS > /dev/null 2>&1 && pass "namespace $MON_NS" || fail "namespace $MON_NS"

# ──────────────────────────── 2. 모니터링 Pod ──────
echo ""
echo "--- 모니터링 Pod ---"
for deploy in otel-collector prometheus loki tempo grafana; do
  READY=$(kubectl get deploy $deploy -n $MON_NS -o jsonpath='{.status.readyReplicas}' 2>/dev/null)
  [ "$READY" = "1" ] && pass "$deploy Ready" || fail "$deploy Not Ready (replicas=$READY)"
done

# ──────────────────────────── 3. Agent Pod ──────
echo ""
echo "--- Agent Pod ---"
for agent in orchestrator search summarizer coder; do
  READY=$(kubectl get deploy agent-$agent -n $K8S_NS -o jsonpath='{.status.readyReplicas}' 2>/dev/null)
  [ -n "$READY" ] && [ "$READY" -ge 1 ] && pass "agent-$agent Ready ($READY)" || fail "agent-$agent Not Ready"
done

# ──────────────────────────── 4. Dapr sidecar ──────
echo ""
echo "--- Dapr sidecar ---"
CONTAINERS=$(kubectl get pods -n $K8S_NS -l app.kubernetes.io/name=agent-orchestrator -o jsonpath='{.items[0].spec.containers[*].name}' 2>/dev/null)
echo "$CONTAINERS" | grep -q "daprd" && pass "Dapr sidecar 주입됨" || pass "Dapr 비활성화 (sidecar 없음)"

# ──────────────────────────── 5. 서비스 연결 ──────
echo ""
echo "--- 서비스 연결 ---"
for svc in agent-orchestrator agent-search agent-summarizer agent-coder; do
  kubectl get svc $svc -n $K8S_NS > /dev/null 2>&1 && pass "svc/$svc 존재" || fail "svc/$svc 미존재"
done

# ──────────────────────────── 6. ConfigMap ──────
echo ""
echo "--- ConfigMap ---"
kubectl get configmap grafana-dashboards -n $MON_NS > /dev/null 2>&1 && pass "grafana-dashboards ConfigMap" || fail "grafana-dashboards ConfigMap 미존재"
DASH_COUNT=$(kubectl get configmap grafana-dashboards -n $MON_NS -o json 2>/dev/null | python3 -c "import sys,json;print(len(json.load(sys.stdin).get('data',{})))" 2>/dev/null)
[ "$DASH_COUNT" = "4" ] && pass "대시보드 $DASH_COUNT개" || fail "대시보드 $DASH_COUNT개 (expected: 4)"

# ──────────────────────────── 7. RBAC ──────
echo ""
echo "--- RBAC ---"
kubectl auth can-i get configmaps --as=system:serviceaccount:$K8S_NS:agent-orchestrator -n $K8S_NS > /dev/null 2>&1 && pass "RBAC: configmaps get 허용" || fail "RBAC: configmaps get 거부"
kubectl auth can-i create deployments --as=system:serviceaccount:$K8S_NS:agent-orchestrator -n $K8S_NS > /dev/null 2>&1 && fail "RBAC: deployments create 허용됨 (보안 문제)" || pass "RBAC: deployments create 거부"

# ──────────────────────────── 8. SecurityContext ──────
echo ""
echo "--- SecurityContext ---"
NON_ROOT=$(kubectl get pods -n $K8S_NS -l app.kubernetes.io/name=agent-orchestrator -o jsonpath='{.items[0].spec.securityContext.runAsNonRoot}' 2>/dev/null)
[ "$NON_ROOT" = "true" ] && pass "runAsNonRoot=true" || fail "runAsNonRoot=$NON_ROOT"

# ──────────────────────────── 9. Grafana 접근 ──────
echo ""
echo "--- Grafana ---"
# port-forward로 테스트
kubectl port-forward -n $MON_NS svc/grafana 13000:3000 > /dev/null 2>&1 &
PF_PID=$!
sleep 5

GF_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:13000/api/health --max-time 5 2>/dev/null)
[ "$GF_STATUS" = "200" ] && pass "Grafana API healthy" || fail "Grafana API status=$GF_STATUS"

# 데이터소스 확인
DS_COUNT=$(curl -s http://localhost:13000/api/datasources --max-time 5 2>/dev/null | python3 -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null)
[ "$DS_COUNT" = "3" ] && pass "데이터소스 $DS_COUNT개 (Prometheus, Loki, Tempo)" || fail "데이터소스 $DS_COUNT개 (expected: 3)"

# 플러그인 확인
for plugin in grafana-exploretraces-app grafana-lokiexplore-app grafana-metricsdrilldown-app; do
  ENABLED=$(curl -s "http://localhost:13000/api/plugins/$plugin/settings" --max-time 5 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('enabled','?'))" 2>/dev/null)
  [ "$ENABLED" = "True" ] && pass "plugin $plugin enabled" || fail "plugin $plugin enabled=$ENABLED"
done

# ──────────────────────────── 10. 쿼리 테스트 ──────
echo ""
echo "--- 쿼리 테스트 ---"
kubectl port-forward -n $K8S_NS svc/agent-orchestrator 18080:8000 > /dev/null 2>&1 &
PF2_PID=$!
sleep 3

QUERY_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:18080/run \
  -H "Content-Type: application/json" \
  -d '{"query": "What is Kubernetes?"}' --max-time 30 2>/dev/null)
[ "$QUERY_STATUS" = "200" ] && pass "orchestrator 쿼리 HTTP 200" || fail "orchestrator 쿼리 HTTP $QUERY_STATUS"

# ──────────────────────────── 11. 메트릭 수집 ──────
echo ""
echo "--- 메트릭 수집 ---"
sleep 5
kubectl port-forward -n $MON_NS svc/prometheus 19090:9090 > /dev/null 2>&1 &
PF3_PID=$!
sleep 3

METRIC_COUNT=$(curl -sG "http://localhost:19090/api/v1/query" --data-urlencode 'query=agent_run_count_total' --max-time 5 2>/dev/null \
  | python3 -c "import sys,json;print(len(json.load(sys.stdin).get('data',{}).get('result',[])))" 2>/dev/null)
[ -n "$METRIC_COUNT" ] && [ "$METRIC_COUNT" -ge 1 ] && pass "Prometheus 메트릭 수집 ($METRIC_COUNT 시리즈)" || fail "Prometheus 메트릭 미수집"

# ──────────────────────────── 12. 트레이스 수집 ──────
echo ""
echo "--- 트레이스 수집 ---"
sleep 5
kubectl port-forward -n $MON_NS svc/tempo 13200:3200 > /dev/null 2>&1 &
PF4_PID=$!
sleep 3

TRACE_COUNT=$(curl -s "http://localhost:13200/api/search?limit=5" --max-time 10 2>/dev/null \
  | python3 -c "import sys,json;print(len(json.load(sys.stdin).get('traces',[])))" 2>/dev/null)
[ -n "$TRACE_COUNT" ] && [ "$TRACE_COUNT" -ge 1 ] && pass "Tempo 트레이스 수집 ($TRACE_COUNT건)" || fail "Tempo 트레이스 미수집"

# ──────────────────────────── 정리 ──────
kill $PF_PID $PF2_PID $PF3_PID $PF4_PID 2>/dev/null
wait $PF_PID $PF2_PID $PF3_PID $PF4_PID 2>/dev/null

# 결과
echo ""
echo "════════════════════════════════════════"
echo "  PASSED: $PASS  FAILED: $FAIL"
echo "════════════════════════════════════════"
[ $FAIL -eq 0 ] && exit 0 || exit 1
