.PHONY: up down build restart logs status clean health stats test-unit query-traces query-logs query-metrics

# ─── 전체 스택 (모니터링 + Agent 4개 + LoadGen) ───
up:
	docker-compose up -d --build

down:
	docker-compose down

restart:
	docker-compose down && docker-compose up -d --build

logs:
	docker-compose logs -f

logs-agents:
	docker-compose logs -f agent-orchestrator agent-search agent-summarizer agent-coder

logs-loadgen:
	docker-compose logs -f loadgen

status:
	docker-compose ps

clean:
	docker-compose down -v --rmi local

# ─── 테스트 ───
test-orchestrator:
	@curl -s -X POST http://localhost:8000/run \
		-H "Content-Type: application/json" \
		-d '{"query": "What is the capital of France?"}' | python3 -m json.tool

test-search:
	@curl -s -X POST http://localhost:8001/run \
		-H "Content-Type: application/json" \
		-d '{"query": "What is Kubernetes?"}' | python3 -m json.tool

test-summarizer:
	@curl -s -X POST http://localhost:8002/run \
		-H "Content-Type: application/json" \
		-d '{"query": "Summarize: OpenTelemetry is a collection of APIs, SDKs, and tools for observability."}' | python3 -m json.tool

test-coder:
	@curl -s -X POST http://localhost:8003/run \
		-H "Content-Type: application/json" \
		-d '{"query": "Write a Python function for binary search"}' | python3 -m json.tool

test-unit:
	@cd agent && .venv/bin/python -m pytest ../tests/test_unit.py -v

test-all:
	@echo "=== Orchestrator ===" && make test-orchestrator
	@echo "\n=== Search ===" && make test-search
	@echo "\n=== Summarizer ===" && make test-summarizer
	@echo "\n=== Coder ===" && make test-coder

health:
	@echo "=== Orchestrator ===" && curl -s http://localhost:8000/health | python3 -m json.tool
	@echo "=== Search ===" && curl -s http://localhost:8001/health | python3 -m json.tool
	@echo "=== Summarizer ===" && curl -s http://localhost:8002/health | python3 -m json.tool
	@echo "=== Coder ===" && curl -s http://localhost:8003/health | python3 -m json.tool

stats:
	@echo "=== Orchestrator ===" && curl -s http://localhost:8000/stats | python3 -m json.tool
	@echo "\n=== Search ===" && curl -s http://localhost:8001/stats | python3 -m json.tool
	@echo "\n=== Summarizer ===" && curl -s http://localhost:8002/stats | python3 -m json.tool
	@echo "\n=== Coder ===" && curl -s http://localhost:8003/stats | python3 -m json.tool

stats-all:
	@echo "=== All Agents Cost Summary ===" && \
	for port in 8000 8001 8002 8003; do \
		curl -s http://localhost:$$port/stats | python3 -c "import sys,json; d=json.load(sys.stdin); t=d['total_tokens']; print(f\"{d['agent_type']:<16} reqs={d['total_requests']:<6} tokens={t['total']:<8} cost=\$${ d['total_cost_usd']:.6f}\")"; \
	done

# ─── Dapr 모드 ───
DAPR_COMPOSE := docker-compose -f docker-compose.dapr.yml

dapr-up:
	$(DAPR_COMPOSE) up -d --build

dapr-down:
	$(DAPR_COMPOSE) down

dapr-restart:
	$(DAPR_COMPOSE) down && $(DAPR_COMPOSE) up -d --build

dapr-status:
	$(DAPR_COMPOSE) ps

dapr-logs:
	$(DAPR_COMPOSE) logs -f orchestrator-dapr search-dapr summarizer-dapr coder-dapr

dapr-logs-all:
	$(DAPR_COMPOSE) logs -f

# ════════════════════════════════════════════════════════════════
# 로컬 K8s (minikube / kind / Docker Desktop)
# ════════════════════════════════════════════════════════════════

HELM_CHART    := ./helm/agent-platform
HELM_RELEASE  := agent-platform
K8S_NS        := agent-platform
MON_NS        := monitoring
VALUES_BASE   := $(HELM_CHART)/values-local-base.yaml
VALUES_LOCAL  := $(HELM_CHART)/values-local.yaml
VALUES_DAPR   := $(HELM_CHART)/values-local-dapr.yaml

# ─── Helm 차트 검증 (클러스터 불필요) ───
helm-lint:
	helm lint $(HELM_CHART)
	helm lint $(HELM_CHART) -f $(VALUES_BASE) -f $(VALUES_LOCAL)
	helm lint $(HELM_CHART) -f $(VALUES_BASE) -f $(VALUES_DAPR)
	@echo "✓ Helm lint 통과"

helm-template:
	helm template $(HELM_RELEASE) $(HELM_CHART) -f $(VALUES_BASE) -f $(VALUES_LOCAL) --namespace $(K8S_NS)

helm-template-dapr:
	helm template $(HELM_RELEASE) $(HELM_CHART) -f $(VALUES_BASE) -f $(VALUES_DAPR) --namespace $(K8S_NS)

helm-validate: helm-lint
	helm template $(HELM_RELEASE) $(HELM_CHART) -f $(VALUES_BASE) -f $(VALUES_LOCAL) --namespace $(K8S_NS) \
		| kubectl apply --dry-run=client -f - 2>&1 || true
	@echo "✓ 검증 완료"

# ─── 이미지 빌드 ───
k8s-build:
	docker build -t agent:local ./agent
	@echo "✓ agent:local 빌드 완료"

k8s-build-grafana:
	docker build -t grafana-custom:local ./k8s-local/grafana-plugins
	@echo "✓ grafana-custom:local 빌드 완료"

k8s-build-all: k8s-build k8s-build-grafana

# ─── 배포 단계 ───
k8s-setup:
	kubectl apply -f k8s-local/namespace.yaml
	@echo "✓ 네임스페이스"

k8s-dashboards:
	kubectl create configmap grafana-dashboards \
		--from-file=grafana/provisioning/dashboards/json/ \
		-n $(MON_NS) --dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ 대시보드 ConfigMap"

k8s-monitoring: k8s-dashboards
	kubectl apply -f k8s-local/monitoring/
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
		kubectl apply -f k8s-local/aoai-secret.yaml
	@echo "✓ AOAI Secret"

k8s-grafana-plugins:
	@for plugin in grafana-exploretraces-app grafana-lokiexplore-app grafana-metricsdrilldown-app; do \
		curl -s -X POST "http://localhost:30300/api/plugins/$$plugin/settings" \
			-H "Content-Type: application/json" -d '{"enabled":true}' > /dev/null 2>&1 \
			&& echo "  ✓ $$plugin" || echo "  - $$plugin (skip)"; \
	done
	@echo "✓ Grafana 플러그인 활성화"

k8s-deploy: k8s-setup k8s-secret
	helm upgrade --install $(HELM_RELEASE) $(HELM_CHART) \
		-f $(VALUES_BASE) -f $(VALUES_LOCAL) \
		--namespace $(K8S_NS) --wait --timeout 3m
	@echo "✓ Agent 배포 완료"

k8s-deploy-dapr: k8s-setup k8s-secret
	kubectl apply -f k8s-local/redis.yaml
	helm upgrade --install $(HELM_RELEASE) $(HELM_CHART) \
		-f $(VALUES_BASE) -f $(VALUES_DAPR) \
		--namespace $(K8S_NS) --wait --timeout 3m
	@echo "✓ Agent (Dapr 모드) 배포 완료"

# ─── 원스텝 배포 ───
k8s-up: k8s-build-all k8s-setup k8s-monitoring k8s-deploy k8s-grafana-plugins
	@echo ""
	@echo "════════════════════════════════════════"
	@echo "  로컬 K8s 배포 완료!"
	@echo "  Grafana: http://localhost:30300"
	@echo "════════════════════════════════════════"

k8s-up-dapr: k8s-build-all k8s-setup k8s-monitoring k8s-deploy-dapr k8s-grafana-plugins
	@echo ""
	@echo "════════════════════════════════════════"
	@echo "  로컬 K8s + Dapr 배포 완료!"
	@echo "  Grafana: http://localhost:30300"
	@echo "════════════════════════════════════════"

# ─── 상태 확인 ───
k8s-status:
	@echo "=== Agent Platform ===" && kubectl get pods,svc -n $(K8S_NS)
	@echo "" && echo "=== Monitoring ===" && kubectl get pods,svc -n $(MON_NS)

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
	kubectl apply -f k8s-local/loadtest-job.yaml
	kubectl wait --for=condition=complete job/loadtest -n $(K8S_NS) --timeout=300s || true
	kubectl logs job/loadtest -n $(K8S_NS)

k8s-chaos:
	kubectl delete job chaos-test -n $(K8S_NS) --ignore-not-found
	kubectl apply -f k8s-local/loadtest-job.yaml
	kubectl wait --for=condition=complete job/chaos-test -n $(K8S_NS) --timeout=180s || true
	kubectl logs job/chaos-test -n $(K8S_NS)

# ─── 정리 ───
k8s-down:
	helm uninstall $(HELM_RELEASE) --namespace $(K8S_NS) || true
	kubectl delete -f k8s-local/redis.yaml --ignore-not-found
	@echo "✓ Agent 제거 (모니터링 유지)"

k8s-clean:
	helm uninstall $(HELM_RELEASE) --namespace $(K8S_NS) || true
	kubectl delete -f k8s-local/monitoring/ --ignore-not-found
	kubectl delete configmap grafana-dashboards -n $(MON_NS) --ignore-not-found
	kubectl delete -f k8s-local/redis.yaml --ignore-not-found
	kubectl delete namespace $(K8S_NS) $(MON_NS) --ignore-not-found
	@echo "✓ 전체 정리 완료"

# ─── 로그 조회 (Loki API) ───
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

# ─── 메트릭 조회 (Prometheus API) ───
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

# ─── 트레이스 조회 (Tempo API) ───
query-traces:
	@echo "=== Recent Traces (Tempo, last 5m) ===" && \
	curl -sG http://localhost:3200/api/search \
		--data-urlencode 'limit=10' \
		--data-urlencode 'start=$(shell python3 -c "import time; print(int(time.time()-300))")' \
		--data-urlencode 'end=$(shell python3 -c "import time; print(int(time.time()))")' \
	| python3 -c "import sys,json; data=json.load(sys.stdin); traces=data.get('traces',[]); [print(f\"  traceID={t['traceID'][:16]}... root={t.get('rootServiceName','?')} spans={t.get('spanSets',[{}])[0].get('matched',0) if t.get('spanSets') else '?'} duration={t.get('durationMs',0)}ms\") for t in traces[:10]]" 2>/dev/null || echo "No traces found"
