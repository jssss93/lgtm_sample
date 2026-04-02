import os

# ──────────────────────────── Config ────────────────────────────
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "ai-agent")
AGENT_TYPE = os.getenv("AGENT_TYPE", "default")
MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "300"))
CACHE_MAX_SIZE = int(os.getenv("CACHE_MAX_SIZE", "100"))
PROMPT_LOG_MAX_LEN = int(os.getenv("PROMPT_LOG_MAX_LEN", "500"))

# ──────────────────────────── Quota ─────────────────────────────
# 사용자/세션별 일일 토큰 제한 (0 = 무제한)
USER_TOKEN_QUOTA = int(os.getenv("USER_TOKEN_QUOTA", "0"))
# 사용자/세션별 일일 비용 제한 USD (0 = 무제한)
USER_COST_QUOTA = float(os.getenv("USER_COST_QUOTA", "0"))

# ──────────────────────────── Agent Profiles ────────────────────
AGENT_PROFILES = {
    "orchestrator": {
        "deployment": "gpt-4.1",
        "system_prompt": (
            "You are an orchestrator agent. Analyze the user's query and decide which "
            "specialist agents to call.\n"
            "- call_search: for factual/knowledge questions\n"
            "- call_summarizer: for text summarization requests\n"
            "- call_coder: for code generation or review tasks\n"
            "You may call multiple agents if the query needs it. "
            "After receiving agent results, synthesize a final answer."
        ),
    },
    "search": {
        "deployment": "gpt-4.1-mini",
        "system_prompt": (
            "You are a search agent. Answer factual questions accurately and concisely. "
            "Provide structured, informative answers. Keep responses under 200 words."
        ),
    },
    "summarizer": {
        "deployment": "gpt-4.1-mini",
        "system_prompt": (
            "You are a summarization agent. Given text, produce a clear and concise summary. "
            "Preserve key facts and main ideas. Keep summaries under 150 words."
        ),
    },
    "coder": {
        "deployment": "gpt-4.1",
        "system_prompt": (
            "You are a code agent. Generate clean, well-commented code. "
            "When reviewing code, identify bugs and suggest improvements. "
            "Always include brief explanations with your code."
        ),
    },
}

# ──────────────────────────── Cost Tracking ─────────────────────
PRICING = {
    "gpt-4.1":      {"prompt": 2.00, "completion": 8.00},
    "gpt-4.1-mini": {"prompt": 0.40, "completion": 1.60},
}

# ──────────────────────────── Sub-Agent URLs ────────────────────
SUB_AGENT_URLS = {
    "call_search": os.getenv("SEARCH_AGENT_URL", "http://agent-search:8000"),
    "call_summarizer": os.getenv("SUMMARIZER_AGENT_URL", "http://agent-summarizer:8000"),
    "call_coder": os.getenv("CODER_AGENT_URL", "http://agent-coder:8000"),
}

# ──────────────────────────── Orchestrator Tools ────────────────
ORCHESTRATOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "call_search",
            "description": "Call the search agent for factual or knowledge questions",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_summarizer",
            "description": "Call the summarizer agent to summarize text",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to summarize"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_coder",
            "description": "Call the coder agent for code generation or review",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The coding task"},
                },
                "required": ["query"],
            },
        },
    },
]
