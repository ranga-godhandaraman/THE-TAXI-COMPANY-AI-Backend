from typing import Literal

from pydantic import BaseModel, Field


StatusLiteral = Literal["ok", "degraded", "error", "not_configured"]


class ComponentHealth(BaseModel):
    status: StatusLiteral
    detail: str | None = None
    latency_ms: float | None = None


class HealthResponse(BaseModel):
    status: StatusLiteral
    service: str
    components: dict[str, ComponentHealth] = Field(default_factory=dict)
