"""
멀티에이전트 시스템 통합 테스트
docker-compose up -d --build 이후 실행:
  python -m pytest tests/test_agents.py -v
"""

import httpx
import pytest

BASE_URLS = {
    "orchestrator": "http://localhost:8000",
    "search": "http://localhost:8001",
    "summarizer": "http://localhost:8002",
    "coder": "http://localhost:8003",
}

TIMEOUT = 60.0


# ─── Health checks ───
@pytest.mark.parametrize("name,url", BASE_URLS.items())
def test_health(name, url):
    resp = httpx.get(f"{url}/health", timeout=10.0)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["agent_type"] == name


# ─── Sub-agent direct calls ───
def test_search_agent():
    resp = httpx.post(
        f"{BASE_URLS['search']}/run",
        json={"query": "What is Python?"},
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_type"] == "search"
    assert data["model"] == "gpt-4.1-mini"
    assert len(data["result"]) > 0
    assert data["tokens"]["prompt"] > 0


def test_summarizer_agent():
    resp = httpx.post(
        f"{BASE_URLS['summarizer']}/run",
        json={"query": "Summarize: Docker is a platform for building and running containers."},
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_type"] == "summarizer"
    assert data["model"] == "gpt-4.1-mini"
    assert len(data["result"]) > 0


def test_coder_agent():
    resp = httpx.post(
        f"{BASE_URLS['coder']}/run",
        json={"query": "Write a hello world function in Python"},
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_type"] == "coder"
    assert data["model"] == "gpt-4.1"
    assert len(data["result"]) > 0


# ─── Orchestrator ───
def test_orchestrator_routes_to_search():
    resp = httpx.post(
        f"{BASE_URLS['orchestrator']}/run",
        json={"query": "What is the capital of Japan?"},
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_type"] == "orchestrator"
    assert "Tokyo" in data["result"] or "tokyo" in data["result"].lower()


def test_orchestrator_routes_to_coder():
    resp = httpx.post(
        f"{BASE_URLS['orchestrator']}/run",
        json={"query": "Write a Python function that checks if a number is prime"},
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_type"] == "orchestrator"
    assert "def" in data["result"] or "prime" in data["result"].lower()


def test_orchestrator_routes_to_summarizer():
    resp = httpx.post(
        f"{BASE_URLS['orchestrator']}/run",
        json={"query": "Summarize the following: Kubernetes is an open-source container orchestration platform that automates deployment, scaling, and management of containerized applications."},
        timeout=TIMEOUT,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_type"] == "orchestrator"
    assert len(data["result"]) > 0
