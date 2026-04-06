#!/bin/bash
# ════════════════════════════════════════════════════════════════
# Helm 차트 검증 테스트 (클러스터 불필요)
# Usage: ./tests/test_helm_values.sh
# ════════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."

CHART="./deploy/helm"
BASE="$CHART/values-local-base.yaml"
LOCAL="$CHART/values-local.yaml"
DAPR="$CHART/values-local-dapr.yaml"
PASS=0; FAIL=0

pass() { echo "  ✅ $1"; PASS=$((PASS+1)); }
fail() { echo "  ❌ $1"; FAIL=$((FAIL+1)); }

echo "=== Helm 차트 검증 ==="

# 1. helm lint
echo ""
echo "--- helm lint ---"
helm lint "$CHART" > /dev/null 2>&1 && pass "default values lint" || fail "default values lint"
helm lint "$CHART" -f "$BASE" -f "$LOCAL" > /dev/null 2>&1 && pass "local values lint" || fail "local values lint"
helm lint "$CHART" -f "$BASE" -f "$DAPR" > /dev/null 2>&1 && pass "dapr values lint" || fail "dapr values lint"

# 2. helm template 렌더링
echo ""
echo "--- helm template ---"
LOCAL_OUT=$(helm template test "$CHART" -f "$BASE" -f "$LOCAL" --namespace test 2>&1)
[ $? -eq 0 ] && pass "local template renders" || fail "local template renders"

DAPR_OUT=$(helm template test "$CHART" -f "$BASE" -f "$DAPR" --namespace test 2>&1)
[ $? -eq 0 ] && pass "dapr template renders" || fail "dapr template renders"

# 3. 에이전트 4개 존재 확인
echo ""
echo "--- 에이전트 확인 ---"
for agent in orchestrator search summarizer coder; do
  echo "$LOCAL_OUT" | grep -q "name: agent-$agent" && pass "local: agent-$agent 존재" || fail "local: agent-$agent 미존재"
done

# 4. Dapr 모드에서 sidecar annotation 존재
echo ""
echo "--- Dapr annotation ---"
echo "$DAPR_OUT" | grep -q 'dapr.io/enabled: "true"' && pass "dapr annotation 존재" || fail "dapr annotation 미존재"
echo "$LOCAL_OUT" | grep -q 'dapr.io/enabled' && fail "local에 dapr annotation 있으면 안됨" || pass "local에 dapr annotation 없음"

# 5. RBAC 리소스 존재
echo ""
echo "--- RBAC ---"
echo "$LOCAL_OUT" | grep -q "kind: ServiceAccount" && pass "ServiceAccount 존재" || fail "ServiceAccount 미존재"
echo "$LOCAL_OUT" | grep -q "kind: Role" && pass "Role 존재" || fail "Role 미존재"
echo "$LOCAL_OUT" | grep -q "kind: RoleBinding" && pass "RoleBinding 존재" || fail "RoleBinding 미존재"

# 6. SecurityContext 확인
echo ""
echo "--- SecurityContext ---"
echo "$LOCAL_OUT" | grep -q "runAsNonRoot: true" && pass "runAsNonRoot 설정" || fail "runAsNonRoot 미설정"
echo "$LOCAL_OUT" | grep -q "allowPrivilegeEscalation: false" && pass "allowPrivilegeEscalation false" || fail "allowPrivilegeEscalation 미설정"

# 7. Dapr trustDomain 확인
echo ""
echo "--- Dapr trustDomain ---"
if echo "$DAPR_OUT" | grep -q "trustDomain"; then
  TRUST=$(echo "$DAPR_OUT" | grep "trustDomain:" | head -1 | awk '{print $2}' | tr -d '"')
  [ "$TRUST" = "public" ] && pass "trustDomain=public" || fail "trustDomain=$TRUST (expected: public)"
else
  pass "accessControl 비활성화 (trustDomain 불필요)"
fi

# 8. preStop hook 존재
echo ""
echo "--- Lifecycle ---"
echo "$LOCAL_OUT" | grep -q "preStop" && pass "preStop hook 존재" || fail "preStop hook 미존재"

# 결과
echo ""
echo "════════════════════════════════════════"
echo "  PASSED: $PASS  FAILED: $FAIL"
echo "════════════════════════════════════════"
[ $FAIL -eq 0 ] && exit 0 || exit 1
