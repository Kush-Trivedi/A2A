from pydantic import Field
from ..base import StrictBaseModel

class HealthCheckResponse(StrictBaseModel):
    status: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)


class IntegrationStatusModel(StrictBaseModel):
    name: str
    status: str = Field(..., description="ok | error | not_configured")
    detail: str = ""
    latency_ms: float = 0.0


class IntegrationHealthResponse(StrictBaseModel):
    overall_status: str = Field(..., description="ok | degraded")
    integrations: list[IntegrationStatusModel] = Field(default_factory=list)
