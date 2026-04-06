{{/*
공통 라벨
*/}}
{{- define "agent-platform.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: agent-platform
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/*
Agent 라벨
*/}}
{{- define "agent-platform.agentLabels" -}}
app.kubernetes.io/name: agent-{{ .name }}
app.kubernetes.io/component: {{ if .isOrchestrator }}orchestrator{{ else }}sub-agent{{ end }}
agent-platform/type: {{ .type }}
agent-platform/model: {{ .model }}
{{- end }}
