.PHONY: health stats test-unit test-eval test-all test-helm test-k8s query-traces query-metrics

# ════════════════════════════════════════════════════════════════
# 변수
# ════════════════════════════════════════════════════════════════

HELM_CHART    := ./infra/helm
HELM_RELEASE  := agent-platform
K8S_NS        := agent-platform
MON_NS        := monitoring
LANGFUSE_NS   := langfuse
KIND_CLUSTER  := lgtm
ORCH_URL      := http://localhost:30800

VALUES_BASE   := $(HELM_CHART)/values-local-base.yaml
VALUES_DAPR   := $(HELM_CHART)/values-local-dapr.yaml

# ════════════════════════════════════════════════════════════════
# kind 클러스터 — Zero-to-Deployed
# ════════════════════════════════════════════════════════════════

# ─── 외부 이미지 사전 다운로드 (병렬) ───
kind-prefetch:
	@echo "외부 이미지 병렬 다운로드 중..."
	@docker pull busybox:latest & \
	docker pull clickhouse/clickhouse-server:24.8-alpine & \
	docker pull grafana/loki:3.7.1 & \
	docker pull grafana/tempo:2.6.1 & \
	docker pull langfuse/langfuse-worker:3 & \
	docker pull langfuse/langfuse:3 & \
	docker pull minio/mc:latest & \
	docker pull minio/minio:latest & \
	docker pull otel/opentelemetry-collector-contrib:0.149.0 & \
	docker pull postgres:16-alpine & \
	docker pull prom/prometheus:v3.10.0 & \
	docker pull redis:7-alpine & \
	docker pull registry.k8s.io/metrics-server/metrics-server:v0.8.1 & \
	wait
	@echo "✓ 외부 이미지 다운로드 완료"

# ─── kind에 외부 이미지 로드 ───
kind-load-external:
	@echo "외부 이미지 kind 로드 중..."
	@kind load docker-image busybox:latest --name $(KIND_CLUSTER) & \
	kind load docker-image clickhouse/clickhouse-server:24.8-alpine --name $(KIND_CLUSTER) & \
	kind load docker-image grafana/loki:3.7.1 --name $(KIND_CLUSTER) & \
	kind load docker-image grafana/tempo:2.6.1 --name $(KIND_CLUSTER) & \
	kind load docker-image langfuse/langfuse-worker:3 --name $(KIND_CLUSTER) & \
	kind load docker-image langfuse/langfuse:3 --name $(KIND_CLUSTER) & \
	kind load docker-image minio/mc:latest --name $(KIND_CLUSTER) & \
	kind load docker-image minio/minio:latest --name $(KIND_CLUSTER) & \
	kind load docker-image otel/opentelemetry-collector-contrib:0.149.0 --name $(KIND_CLUSTER) & \
	kind load docker-image postgres:16-alpine --name $(KIND_CLUSTER) & \
	kind load docker-image prom/prometheus:v3.10.0 --name $(KIND_CLUSTER) & \
	kind load docker-image redis:7-alpine --name $(KIND_CLUSTER) & \
	kind load docker-image registry.k8s.io/metrics-server/metrics-server:v0.8.1 --name $(KIND_CLUSTER) & \
	wait
	@echo "✓ 외부 이미지 kind 로드 완료"

# ─── 원스텝 배포 (kind 기준) — 스테이지별 병렬 실행 ───
#
#  Stage 1 (병렬): 외부 이미지 pull  +  로컬 이미지 빌드
#  Stage 2 (순차): kind 클러스터 생성
#  Stage 3 (병렬): 이미지 kind 주입  +  볼륨 디렉토리 생성
#  Stage 4 (병렬): 애드온 설치  +  네임스페이스 생성  +  Langfuse 배포
#  Stage 5 (병렬): 모니터링 스택 배포  +  Agent 배포
#  Stage 6 (순차): Grafana 플러그인 활성화
#
kind-up:
	@echo "[1/6] 외부 이미지 다운로드 + 로컬 이미지 빌드 (병렬)..."
	@$(MAKE) kind-prefetch & $(MAKE) k8s-build-all & wait
	@echo "[2/6] kind 클러스터 생성..."
	@$(MAKE) kind-create
	@echo "[3/6] 이미지 kind 주입 + 볼륨 디렉토리 생성 (병렬)..."
	@$(MAKE) kind-load & $(MAKE) kind-load-external & $(MAKE) kind-volume & wait
	@echo "[4/6] 애드온 설치 + 네임스페이스 + Langfuse 배포 (병렬)..."
	@$(MAKE) kind-addons & $(MAKE) k8s-setup & $(MAKE) k8s-langfuse & wait
	@echo "[5/6] 모니터링 스택 + Agent 배포 (병렬)..."
	@$(MAKE) k8s-monitoring & $(MAKE) k8s-deploy & wait
	@echo "[6/6] Grafana 플러그인 활성화..."
	@$(MAKE) k8s-grafana-plugins
	@echo ""
	@echo "════════════════════════════════════════"
	@echo "  kind 클러스터 풀 배포 완료!"
	@echo "  Orchestrator: http://localhost:30800"
	@echo "  Grafana:      http://localhost:30400"
	@echo "  Langfuse:     http://localhost:30401"
	@echo "════════════════════════════════════════"

# ─── kind 클러스터 생성 ───
kind-create:
	@kind get clusters 2>/dev/null | grep -q "^$(KIND_CLUSTER)$$" \
		&& echo "⚠  클러스터 '$(KIND_CLUSTER)' 이미 존재 — 건너뜀" \
		|| kind create cluster --name $(KIND_CLUSTER) --config infra/kind/kind-config.yaml
	kubectl wait --for=condition=Ready node --all --timeout=90s
	@echo "✓ kind 클러스터 ($(KIND_CLUSTER))"

# ─── kind에 이미지 로드 ───
kind-load:
	kind load docker-image agent:local --name $(KIND_CLUSTER)
	kind load docker-image grafana-custom:local --name $(KIND_CLUSTER)
	@echo "✓ 이미지 kind 로드"

# ─── hostPath 볼륨 디렉토리 생성 ───
kind-volume:
	mkdir -p ../lgtm_volume/{prometheus,loki,tempo,grafana}
	@echo "✓ hostPath 볼륨 디렉토리 (../lgtm_volume/)"

# ─── 애드온 설치 ───
kind-addons: kind-metrics-server kind-dapr kind-keda
	@echo "✓ 애드온 설치 완료 (metrics-server + Dapr + KEDA)"

kind-metrics-server:
	kubectl apply -f infra/k8s/metrics-server.yaml
	kubectl wait --for=condition=Available deployment/metrics-server -n kube-system --timeout=90s
	@echo "✓ metrics-server"

kind-dapr:
	helm repo add dapr https://dapr.github.io/helm-charts --force-update 2>/dev/null || true
	helm upgrade --install dapr dapr/dapr \
		--namespace dapr-system --create-namespace \
		--wait --timeout 5m
	@echo "✓ Dapr 설치"

kind-keda:
	helm repo add kedacore https://kedacore.github.io/charts --force-update 2>/dev/null || true
	helm upgrade --install keda kedacore/keda \
		--namespace keda --create-namespace \
		--wait --timeout 3m
	@echo "✓ KEDA 설치"

# ─── kind 클러스터 삭제 ───
kind-clean:
	kind delete cluster --name $(KIND_CLUSTER)
	@echo "✓ kind 클러스터 삭제"

# ─── kind 상태 확인 ───
kind-status:
	@echo "=== kind clusters ===" && kind get clusters 2>/dev/null || echo "(없음)"
	@kubectl cluster-info --context kind-$(KIND_CLUSTER) 2>/dev/null || true

# ════════════════════════════════════════════════════════════════
# 로컬 K8s (기존 클러스터 — Docker Desktop / OrbStack 등)
# ════════════════════════════════════════════════════════════════

# ─── 원스텝 배포 (기존 클러스터 기준) ───
k8s-up: k8s-build-all k8s-setup k8s-monitoring k8s-deploy k8s-langfuse k8s-grafana-plugins
	@echo ""
	@echo "════════════════════════════════════════"
	@echo "  로컬 K8s 배포 완료!"
	@echo "  Orchestrator: http://localhost:30800"
	@echo "  Grafana:      http://localhost:30400"
	@echo "  Langfuse:     http://localhost:30401"
	@echo "════════════════════════════════════════"

# ─── 이미지 빌드 ───
k8s-build:
	docker build -t agent:local ./apps/agent
	@echo "✓ agent:local 빌드 완료"

k8s-build-grafana:
	docker build -t grafana-custom:local ./infra/k8s/grafana-plugins
	@echo "✓ grafana-custom:local 빌드 완료"

k8s-build-all: k8s-build k8s-build-grafana

# ─── 배포 단계 ───
k8s-setup:
	kubectl apply -f infra/k8s/namespace.yaml
	@echo "✓ 네임스페이스"

k8s-dashboards:
	kubectl create configmap grafana-dashboards \
		--from-file=infra/grafana/provisioning/dashboards/json/ \
		-n $(MON_NS) --dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ 대시보드 ConfigMap"

k8s-monitoring: k8s-dashboards
	kubectl apply -f infra/k8s/monitoring/
	kubectl wait --for=condition=available deployment/tempo -n $(MON_NS) --timeout=120s
	kubectl wait --for=condition=available deployment/loki -n $(MON_NS) --timeout=120s
	kubectl wait --for=condition=available deployment/prometheus -n $(MON_NS) --timeout=120s
	kubectl rollout restart deployment/otel-collector -n $(MON_NS)
	kubectl wait --for=condition=available deployment/otel-collector -n $(MON_NS) --timeout=120s
	kubectl wait --for=condition=available deployment/grafana -n $(MON_NS) --timeout=120s
	@echo "✓ 모니터링 스택"

k8s-secret:
	@kubectl get secret aoai-secret -n $(K8S_NS) > /dev/null 2>&1 || \
		([ -f .env ] && source .env && kubectl create secret generic aoai-secret \
			--from-literal=api-key="$$AZURE_OPENAI_API_KEY" \
			--from-literal=endpoint="$$AZURE_OPENAI_ENDPOINT" \
			-n $(K8S_NS)) || \
		kubectl apply -f infra/k8s/aoai-secret.yaml
	@echo "✓ AOAI Secret"
	@kubectl get secret langfuse-secret -n $(K8S_NS) > /dev/null 2>&1 || \
		kubectl create secret generic langfuse-secret \
			--from-literal=public-key="pk-lf-local-dev-auto-init-key" \
			--from-literal=secret-key="sk-lf-local-dev-auto-init-key" \
			-n $(K8S_NS)
	@echo "✓ Langfuse Secret (agent-platform)"

k8s-langfuse:
	kubectl apply -f infra/k8s/langfuse.yaml
	kubectl wait --for=condition=available deployment/langfuse-postgres -n $(LANGFUSE_NS) --timeout=120s
	kubectl wait --for=condition=available deployment/langfuse-clickhouse -n $(LANGFUSE_NS) --timeout=180s
	kubectl wait --for=condition=available deployment/langfuse-minio -n $(LANGFUSE_NS) --timeout=120s
	kubectl wait --for=condition=available deployment/langfuse -n $(LANGFUSE_NS) --timeout=180s
	@echo "✓ Langfuse: http://localhost:30401  (admin@local.dev / Admin1234!)"

k8s-grafana-plugins:
	@for plugin in grafana-exploretraces-app grafana-lokiexplore-app grafana-metricsdrilldown-app; do \
		curl -s -X POST "http://localhost:30400/api/plugins/$$plugin/settings" \
			-H "Content-Type: application/json" -d '{"enabled":true}' > /dev/null 2>&1 \
			&& echo "  ✓ $$plugin" || echo "  - $$plugin (skip)"; \
	done
	@echo "✓ Grafana 플러그인 활성화"

k8s-deploy: k8s-setup k8s-secret
	kubectl apply -f infra/k8s/redis.yaml
	helm upgrade --install $(HELM_RELEASE) $(HELM_CHART) \
		-f $(VALUES_BASE) -f $(VALUES_DAPR) \
		--namespace $(K8S_NS) --wait --timeout 3m
	@echo "✓ Agent 배포 완료 (Dapr 모드)"

# ─── 상태 확인 ───
k8s-status:
	@echo "=== Agent Platform ===" && kubectl get pods,svc -n $(K8S_NS)
	@echo "" && echo "=== Monitoring ===" && kubectl get pods,svc -n $(MON_NS)
	@echo "" && echo "=== Langfuse ===" && kubectl get pods,svc -n $(LANGFUSE_NS)

k8s-logs:
	kubectl logs -n $(K8S_NS) -l app.kubernetes.io/part-of=agent-platform --tail=50 -f

k8s-port-forward:
	@pkill -f "kubectl port-forward" 2>/dev/null; sleep 1
	@echo "Port forwarding (Ctrl+C 종료)..."
	@echo "  Orchestrator: http://localhost:8000"
	@echo "  Grafana:      http://localhost:3000"
	@echo "  Prometheus:   http://localhost:9090"
	kubectl port-forward -n $(K8S_NS) svc/agent-orchestrator 8000:8000 &
	kubectl port-forward -n $(MON_NS) svc/grafana 3000:3000 &
	kubectl port-forward -n $(MON_NS) svc/prometheus 9090:9090 &
	@wait

# ─── 정리 ───
k8s-down:
	helm uninstall $(HELM_RELEASE) --namespace $(K8S_NS) || true
	kubectl delete -f infra/k8s/redis.yaml --ignore-not-found
	@echo "✓ Agent 제거 (모니터링 유지)"

k8s-clean:
	helm uninstall $(HELM_RELEASE) --namespace $(K8S_NS) || true
	kubectl delete -f infra/k8s/monitoring/ --ignore-not-found
	kubectl delete configmap grafana-dashboards -n $(MON_NS) --ignore-not-found
	kubectl delete -f infra/k8s/redis.yaml --ignore-not-found
	kubectl delete -f infra/k8s/langfuse.yaml --ignore-not-found
	kubectl delete namespace $(K8S_NS) $(MON_NS) $(LANGFUSE_NS) --ignore-not-found
	@echo "✓ 전체 정리 완료"

# ════════════════════════════════════════════════════════════════
# Helm 차트 검증 (클러스터 불필요)
# ════════════════════════════════════════════════════════════════

helm-lint:
	helm lint $(HELM_CHART)
	helm lint $(HELM_CHART) -f $(VALUES_BASE) -f $(VALUES_DAPR)
	@echo "✓ Helm lint 통과"

helm-template:
	helm template $(HELM_RELEASE) $(HELM_CHART) -f $(VALUES_BASE) -f $(VALUES_DAPR) --namespace $(K8S_NS)

helm-validate: helm-lint
	helm template $(HELM_RELEASE) $(HELM_CHART) -f $(VALUES_BASE) -f $(VALUES_DAPR) --namespace $(K8S_NS) \
		| kubectl apply --dry-run=client -f - 2>&1 || true
	@echo "✓ 검증 완료"

# ════════════════════════════════════════════════════════════════
# 에이전트 테스트 (NodePort 경유)
# ════════════════════════════════════════════════════════════════

test-orchestrator:
	@curl -s -X POST $(ORCH_URL)/run \
		-H "Content-Type: application/json" \
		-d '{"query": "What is the capital of France?"}' | python3 -m json.tool

health:
	@curl -s $(ORCH_URL)/health | python3 -m json.tool

stats:
	@curl -s $(ORCH_URL)/stats | python3 -m json.tool

test-unit:
	@cd apps/agent && .venv/bin/python -m pytest ../../tests/test_unit.py -v

test-eval:
	@cd apps/agent && .venv/bin/python -m pytest ../../tests/test_eval.py -v --asyncio-mode=auto

# ─── 테스트 ───
test-helm:
	@./tests/test_helm_values.sh

test-k8s:
	@./tests/test_k8s_smoke.sh

k8s-test-rbac:
	@echo "=== RBAC 검증 ==="
	@for agent in orchestrator search summarizer coder; do \
		echo "--- agent-$$agent ---"; \
		kubectl auth can-i get configmaps --as=system:serviceaccount:$(K8S_NS):agent-$$agent -n $(K8S_NS) && echo "  configmaps get: 허용" || echo "  configmaps get: 거부"; \
		kubectl auth can-i create deployments --as=system:serviceaccount:$(K8S_NS):agent-$$agent -n $(K8S_NS) && echo "  deployments create: 허용 (문제!)" || echo "  deployments create: 거부 (정상)"; \
	done

k8s-test-security:
	@echo "=== SecurityContext ==="
	@kubectl get pods -n $(K8S_NS) -l app.kubernetes.io/part-of=agent-platform \
		-o custom-columns='NAME:.metadata.name,NON_ROOT:.spec.securityContext.runAsNonRoot,PRIV_ESC:.spec.containers[0].securityContext.allowPrivilegeEscalation'

# ─── 부하/장애 테스트 ───
k8s-loadtest:
	kubectl delete job loadtest -n $(K8S_NS) --ignore-not-found
	kubectl apply -f infra/k8s/loadtest-job.yaml
	kubectl wait --for=condition=complete job/loadtest -n $(K8S_NS) --timeout=300s || true
	kubectl logs job/loadtest -n $(K8S_NS)

k8s-chaos:
	kubectl delete job chaos-test -n $(K8S_NS) --ignore-not-found
	kubectl apply -f infra/k8s/loadtest-job.yaml
	kubectl wait --for=condition=complete job/chaos-test -n $(K8S_NS) --timeout=180s || true
	kubectl logs job/chaos-test -n $(K8S_NS)

# ════════════════════════════════════════════════════════════════
# 로그 / 메트릭 / 트레이스 조회
# ════════════════════════════════════════════════════════════════

logs-loki:
	@echo "=== Recent agent logs (Loki, last 5m) ===" && \
	curl -sG http://localhost:3100/loki/api/v1/query_range \
		--data-urlencode 'query={service_name=~"agent-.*"}' \
		--data-urlencode 'limit=20' \
		--data-urlencode "start=$$(python3 -c 'import time; print(int((time.time()-300)*1e9))')" \
		--data-urlencode "end=$$(python3 -c 'import time; print(int(time.time()*1e9))')" \
	| python3 -c "import sys,json; data=json.load(sys.stdin); results=data.get('data',{}).get('result',[]); [print(f\"[{s.get('stream',{}).get('service_name','?')}] {v[1][:200]}\") for s in results for v in s.get('values',[])]"

logs-errors:
	@echo "=== Error logs (Loki, last 10m) ===" && \
	curl -sG http://localhost:3100/loki/api/v1/query_range \
		--data-urlencode 'query={service_name=~"agent-.*"} |= "ERROR"' \
		--data-urlencode 'limit=20' \
		--data-urlencode "start=$$(python3 -c 'import time; print(int((time.time()-600)*1e9))')" \
		--data-urlencode "end=$$(python3 -c 'import time; print(int(time.time()*1e9))')" \
	| python3 -c "import sys,json; data=json.load(sys.stdin); results=data.get('data',{}).get('result',[]); [print(f\"[{s.get('stream',{}).get('service_name','?')}] {v[1][:200]}\") for s in results for v in s.get('values',[])]" || echo "No errors found"

query-metrics:
	@echo "=== Agent Run Count ===" && \
	curl -sG http://localhost:9090/api/v1/query --data-urlencode 'query=agent_run_count_total' \
	| python3 -c "import sys,json; data=json.load(sys.stdin); [print(f\"  {r['metric'].get('agent_type','?')}: {r['value'][1]}\") for r in data.get('data',{}).get('result',[])]"
	@echo "\n=== Token Usage ===" && \
	curl -sG http://localhost:9090/api/v1/query --data-urlencode 'query=llm_token_usage_total' \
	| python3 -c "import sys,json; data=json.load(sys.stdin); [print(f\"  {r['metric'].get('llm_model','?')} ({r['metric'].get('type','?')}): {r['value'][1]}\") for r in data.get('data',{}).get('result',[])]"
	@echo "\n=== LLM Call Duration (avg, last 5m) ===" && \
	curl -sG http://localhost:9090/api/v1/query --data-urlencode 'query=rate(llm_call_duration_seconds_sum[5m]) / rate(llm_call_duration_seconds_count[5m])' \
	| python3 -c "import sys,json; data=json.load(sys.stdin); [print(f\"  {r['metric'].get('agent_type','?')} ({r['metric'].get('llm_model','?')}): {float(r['value'][1]):.2f}s\") for r in data.get('data',{}).get('result',[])]"

query-traces:
	@echo "=== Recent Traces (Tempo, last 5m) ===" && \
	curl -sG http://localhost:3200/api/search \
		--data-urlencode 'limit=10' \
		--data-urlencode 'start=$(shell python3 -c "import time; print(int(time.time()-300))")' \
		--data-urlencode 'end=$(shell python3 -c "import time; print(int(time.time()))")' \
	| python3 -c "import sys,json; data=json.load(sys.stdin); traces=data.get('traces',[]); [print(f\"  traceID={t['traceID'][:16]}... root={t.get('rootServiceName','?')} spans={t.get('spanSets',[{}])[0].get('matched',0) if t.get('spanSets') else '?'} duration={t.get('durationMs',0)}ms\") for t in traces[:10]]" 2>/dev/null || echo "No traces found"
