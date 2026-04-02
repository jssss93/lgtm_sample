from pydantic import BaseModel


class AgentRequest(BaseModel):
    query: str
    context: str | None = None
    params: dict[str, str | int | float | bool] | None = None
    model_override: str | None = None


class AgentResponse(BaseModel):
    agent_type: str
    model: str
    result: str
    tokens: dict | None = None
    cost_usd: float | None = None
    cached: bool = False
    retries: int = 0
