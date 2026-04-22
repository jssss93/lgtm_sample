# loadgen/ — 부하 생성기

자동 트래픽 생성기. K8s Job(`make k8s-loadtest`)으로 실행.

## 파일

| 파일 | 역할 |
|------|------|
| `run.py` | 부하 생성 스크립트. 가중치 기반 쿼리 풀에서 랜덤 선택 → orchestrator에 POST /run |
| `Dockerfile` | python:3.12-slim + httpx |

## 동작

1. `wait_for_orchestrator()`: /health 폴링 (5초 간격, 최대 60초)
2. 쿼리 풀: 일반 23개(70%) + Heavy 7개(30%)
3. 인터벌: 일반 2~8s jitter, Heavy 1~3s jitter
4. 최대 실행: `MAX_DURATION` (기본 300초)
5. 누적 비용 추적 + 로깅

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ORCHESTRATOR_URL` | `http://agent-orchestrator:8000` | 대상 orchestrator |
| `INTERVAL` | `8` | 요청 간 기본 간격 (초) |
| `MAX_DURATION` | `300` | 최대 실행 시간 (초) |
