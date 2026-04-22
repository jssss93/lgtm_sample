# agent-template/ — 새 Agent 스캐폴딩 (Cookiecutter)

새 에이전트를 빠르게 생성하는 템플릿. 플랫폼 공유 모듈 전체(Clean Architecture 계층 포함)를 자동 복사한다.

## 사용법

```bash
pip install cookiecutter
cookiecutter agent-template \
  --no-input \
  agent_name=reviewer \
  agent_type=reviewer \
  model=gpt-4.1-mini \
  port=8004 \
  system_prompt="You are a code reviewer."
```

## 파일

| 파일 | 역할 |
|------|------|
| `cookiecutter.json` | 템플릿 변수 정의 (agent_name, agent_type, model, port, system_prompt, max_response_words) |
| `hooks/post_gen_project.sh` | 생성 후 훅: `../agent/`에서 플랫폼 모듈 전체 복사 (아래 목록 참고) + Helm values 추가 안내 출력 |
| `{{cookiecutter.agent_name}}/app.py` | 생성될 에이전트 앱 (sub-agent 전용, `SubAgentUseCase`의 system_prompt만 수정) |
| `{{cookiecutter.agent_name}}/Dockerfile` | 컨테이너 빌드 |
| `{{cookiecutter.agent_name}}/requirements.txt` | Python 의존성 |

## 복사되는 플랫폼 모듈

```
domain/               ← CacheBackend, LLMProvider, MetricsRecorder, EventPublisher ABC + 값 객체
application/          ← SubAgentUseCase, OrchestratorUseCase
infrastructure/       ← AzureOpenAIProvider, MemoryCacheBackend, DaprCacheBackend, OTelMetricsRecorder 등
container.py          ← DI 조립 (build_use_case)
cache.py              ← 하위호환 퍼사드
llm.py                ← 하위호환 퍼사드
config.py             ← 환경변수, 프로필, 가격표
models.py             ← AgentRequest / AgentResponse Pydantic 스키마
stats.py              ← 비용 추적, 쿼터 관리
otel_setup.py         ← OTel SDK 초기화
```

## 생성 후 단계

1. `app.py`를 열어 AGENT_PROFILES에 새 에이전트 프로필 추가 (`config.py`)
2. `container.py`의 `build_use_case()`에 새 에이전트 타입 분기 추가 (필요 시)
3. `infra/helm/values.yaml`의 agents 배열에 항목 추가 (post_gen 스크립트가 안내)
4. K8s: `make k8s-up`으로 배포
