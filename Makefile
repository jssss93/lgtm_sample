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
