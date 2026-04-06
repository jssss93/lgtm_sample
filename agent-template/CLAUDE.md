# agent-template/ — 새 Agent 스캐폴딩 (Cookiecutter)

새 에이전트를 빠르게 생성하는 템플릿. 플랫폼 공유 모듈을 자동 복사한다.

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
| `hooks/post_gen_project.sh` | 생성 후 훅: `../agent/`에서 공유 모듈 6개(config, llm, cache, models, stats, otel_setup) 복사 + docker-compose 추가 안내 출력 |
| `{{cookiecutter.agent_name}}/app.py` | 생성될 에이전트 앱 (sub-agent 전용, '비즈니스 로직' 섹션만 수정) |
| `{{cookiecutter.agent_name}}/Dockerfile` | 컨테이너 빌드 |
| `{{cookiecutter.agent_name}}/requirements.txt` | Python 의존성 |

## 생성 후 단계

1. `app.py`의 '비즈니스 로직' 섹션 수정
2. `docker-compose.dapr.yml`에 서비스 + Dapr sidecar 추가 (post_gen 스크립트가 안내)
3. K8s: `deploy/helm/values.yaml`의 agents 배열에 항목 추가
