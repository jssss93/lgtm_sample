#!/bin/bash
# 플랫폼 공유 모듈을 새 agent 디렉토리로 복사
PLATFORM_AGENT_DIR="$(dirname "$(pwd)")/../agent"

for f in config.py llm.py cache.py models.py stats.py otel_setup.py; do
    if [ -f "$PLATFORM_AGENT_DIR/$f" ]; then
        cp "$PLATFORM_AGENT_DIR/$f" .
        echo "  Copied platform module: $f"
    fi
done

echo ""
echo "✅ Agent '{{ cookiecutter.agent_name }}' 생성 완료!"
echo ""
echo "다음 단계:"
echo "  1. app.py 의 '비즈니스 로직' 섹션을 수정하세요"
echo "  2. docker-compose.dapr.yml 에 다음 서비스를 추가하세요:"
echo ""
echo "  agent-{{ cookiecutter.agent_name }}:"
echo "    build: ./{{ cookiecutter.agent_name }}"
echo "    env_file: .env"
echo "    environment:"
echo "      - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317"
echo "      - OTEL_SERVICE_NAME=agent-{{ cookiecutter.agent_name }}"
echo "      - AGENT_TYPE={{ cookiecutter.agent_type }}"
echo "      - USE_DAPR=true"
echo "      - DAPR_HTTP_PORT=3500"
echo "    ports:"
echo "      - \"{{ cookiecutter.port }}:8000\""
echo ""
echo "  {{ cookiecutter.agent_name }}-dapr:"
echo "    image: daprio/daprd:1.17.3"
echo "    command: [\"./daprd\","
echo "      \"--app-id\", \"agent-{{ cookiecutter.agent_name }}\","
echo "      \"--app-port\", \"8000\","
echo "      \"--dapr-http-port\", \"3500\","
echo "      \"--dapr-grpc-port\", \"50001\","
echo "      \"--placement-host-address\", \"placement:50006\","
echo "      \"--config\", \"/config/dapr-config.yaml\","
echo "      \"--resources-path\", \"/components\"]"
echo "    volumes:"
echo "      - ./dapr-components:/components"
echo "      - ./dapr-components/dapr-config.yaml:/config/dapr-config.yaml"
echo "    network_mode: \"service:agent-{{ cookiecutter.agent_name }}\""
