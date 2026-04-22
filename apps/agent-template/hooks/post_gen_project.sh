#!/bin/bash
# 플랫폼 공유 모듈(Clean Architecture 계층 포함)을 새 agent 디렉토리로 복사
PLATFORM_AGENT_DIR="$(dirname "$(pwd)")/../agent"

# 단일 파일 복사 (퍼사드 + 설정 + 불변 모듈)
for f in config.py llm.py cache.py models.py stats.py otel_setup.py container.py; do
    if [ -f "$PLATFORM_AGENT_DIR/$f" ]; then
        cp "$PLATFORM_AGENT_DIR/$f" .
        echo "  Copied platform module: $f"
    fi
done

# Clean Architecture 계층 디렉토리 복사
for d in domain application infrastructure; do
    if [ -d "$PLATFORM_AGENT_DIR/$d" ]; then
        cp -r "$PLATFORM_AGENT_DIR/$d" .
        echo "  Copied platform layer: $d/"
    fi
done

echo ""
echo "✅ Agent '{{ cookiecutter.agent_name }}' 생성 완료!"
echo ""
echo "다음 단계:"
echo "  1. config.py 의 AGENT_PROFILES 에 '{{ cookiecutter.agent_type }}' 프로필을 추가하세요"
echo "  2. app.py 의 '비즈니스 로직' 섹션을 수정하세요"
echo "  3. infra/helm/values.yaml 의 agents 배열에 다음 항목을 추가하세요:"
echo ""
echo "  - name: {{ cookiecutter.agent_name }}"
echo "    type: {{ cookiecutter.agent_type }}"
echo "    model: {{ cookiecutter.model }}"
echo "    replicas: 1"
echo "    port: {{ cookiecutter.port }}"
echo ""
echo "  4. make k8s-up 으로 배포하세요"
