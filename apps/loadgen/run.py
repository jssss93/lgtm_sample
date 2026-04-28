import os
import random
import time

import httpx

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")
INTERVAL = int(os.getenv("INTERVAL", "8"))
MAX_DURATION = int(os.getenv("MAX_DURATION", "300"))  # 최대 실행 시간 (초), 기본 5분

# Normal queries
NORMAL_QUERIES = [
    "What is the capital of South Korea?",
    "Explain how distributed tracing works in microservices",
    "What are the key differences between Prometheus and InfluxDB?",
    "What is the CAP theorem in distributed systems?",
    "How does the OpenTelemetry Collector process telemetry data?",
    "Summarize the following: Kubernetes is an open-source container orchestration platform that automates deployment, scaling, and management of containerized applications.",
    "Summarize the following: OpenTelemetry is a collection of APIs, SDKs, and tools for observability.",
    "Write a Python function that implements binary search on a sorted list",
    "Write a FastAPI health check endpoint that returns the current timestamp",
    "Review this code and find the bug: def avg(nums): return sum(nums) / len(nums)",
]

# Heavy queries
HEAVY_QUERIES = [
    "What is gRPC? Write a complete Python gRPC server and client example with error handling, then summarize the key concepts.",
    "Explain the circuit breaker pattern in detail, write a production-ready implementation in Python with retry logic, backoff, and monitoring hooks, then summarize when to use it.",
    "Search for best practices in Python logging, then summarize the top 10 key points, and write a complete logging setup example with structured logging, rotation, and OpenTelemetry integration.",
    "Write a complete REST API in Python using FastAPI with the following features: user registration, login with JWT tokens, CRUD operations for a todo list, input validation, error handling, database models using SQLAlchemy, and comprehensive docstrings. Include all imports and make it runnable.",
    "Write a comprehensive Python module that implements a thread-safe LRU cache with TTL expiration, size limits, hit/miss statistics, and eviction callbacks. Include type hints, docstrings, unit tests using pytest, and usage examples.",
    "Summarize the following in detail: " + " ".join([
        "Microservices architecture is a software development approach where applications are built as a collection of small, independent services.",
        "Each service runs in its own process, communicates via lightweight mechanisms like HTTP or messaging queues, and can be deployed independently.",
        "This approach contrasts with monolithic architecture where all functionality is tightly coupled in a single deployable unit.",
        "Benefits include independent scaling, technology diversity, fault isolation, and easier team organization around business capabilities.",
        "Challenges include distributed system complexity, data consistency across services, network latency, service discovery, load balancing, and operational overhead.",
        "Common patterns include API Gateway, service mesh, event sourcing, CQRS, saga pattern for distributed transactions, and circuit breaker for fault tolerance.",
        "Observability is critical in microservices and typically involves three pillars: structured logging with correlation IDs, distributed tracing with tools like Jaeger or Tempo, and metrics collection with Prometheus.",
        "Container orchestration platforms like Kubernetes help manage the lifecycle of microservices including deployment, scaling, and self-healing.",
        "Service mesh technologies like Istio or Linkerd provide infrastructure-level capabilities for service-to-service communication, security, and observability.",
        "The LGTM stack (Loki, Grafana, Tempo, Mimir) provides a complete open-source observability solution specifically designed for cloud-native microservices environments.",
    ]),
]

QUERY_POOL = [(q, "normal") for q in NORMAL_QUERIES] + [(q, "heavy") for q in HEAVY_QUERIES]
WEIGHTS = [0.7 / len(NORMAL_QUERIES)] * len(NORMAL_QUERIES) + [0.3 / len(HEAVY_QUERIES)] * len(HEAVY_QUERIES)


def wait_for_orchestrator(max_wait: int = 120):
    """Health check 폴링으로 orchestrator 준비 대기."""
    print(f"Waiting for orchestrator at {ORCHESTRATOR_URL}/health ...")
    start = time.time()
    while time.time() - start < max_wait:
        try:
            resp = httpx.get(f"{ORCHESTRATOR_URL}/health", timeout=5.0)
            if resp.status_code == 200:
                print("Orchestrator is ready!")
                return
        except Exception:
            pass
        time.sleep(3)
    print(f"WARNING: Orchestrator not ready after {max_wait}s, starting anyway...")


def main():
    print(f"Load generator started — target: {ORCHESTRATOR_URL}, interval: {INTERVAL}s, max_duration: {MAX_DURATION}s")
    print(f"  Normal queries: {len(NORMAL_QUERIES)}, Heavy queries: {len(HEAVY_QUERIES)}")

    wait_for_orchestrator()

    request_count = 0
    total_cost = 0.0
    run_start = time.time()

    while time.time() - run_start < MAX_DURATION:
        query, qtype = random.choices(QUERY_POOL, weights=WEIGHTS, k=1)[0]
        request_count += 1

        tag = "HEAVY" if qtype == "heavy" else "NORMAL"
        print(f"\n[#{request_count}] {tag} Q: {query[:80]}...")

        try:
            start = time.time()
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(f"{ORCHESTRATOR_URL}/run", json={"query": query})
            elapsed = time.time() - start

            if resp.status_code == 200:
                data = resp.json()
                cost = data.get("cost_usd", 0) or 0
                total_cost += cost
                tokens = data.get("tokens", {})
                print(f"  [{resp.status_code}] {elapsed:.1f}s | tokens: {tokens} | cost: ${cost:.6f}")
                print(f"  -> {data.get('result', '')[:150]}")
                print(f"  [cumulative] requests={request_count}, total_cost=${total_cost:.6f}")
            else:
                print(f"  [{resp.status_code}] {elapsed:.1f}s | {resp.text[:100]}")
        except Exception as e:
            print(f"  [ERR] {e}")

        if qtype == "heavy":
            jitter = random.uniform(1.0, 3.0)
        else:
            jitter = random.uniform(2.0, INTERVAL)
        time.sleep(jitter)

    elapsed = round(time.time() - run_start, 1)
    print(f"\nLoad generator finished — {elapsed}s elapsed, {request_count} requests, total_cost=${total_cost:.6f}")


if __name__ == "__main__":
    main()
