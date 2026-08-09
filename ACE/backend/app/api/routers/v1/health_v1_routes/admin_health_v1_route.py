from fastapi import APIRouter, Depends

from .....dto.common import (
    ApiEnvelope,
    IntegrationHealthResponse,
    IntegrationStatusModel,
)
from .....security.authorization import require_permission
from .....services.health import IntegrationHealthService, IntegrationProbeResult
from ....dependencies import provide_integration_health_service

admin_health_v1_router = APIRouter(prefix="/admin/health", tags=["Admin / Health"])

_HEALTH_OBJ = "/api/v1/admin/health"


def _to_model(result: IntegrationProbeResult) -> IntegrationStatusModel:
    return IntegrationStatusModel(
        name=result.name,
        status=result.status,
        detail=result.detail,
        latency_ms=result.latency_ms,
    )


@admin_health_v1_router.get(
    "/integrations",
    response_model=ApiEnvelope[IntegrationHealthResponse],
    dependencies=[Depends(require_permission(_HEALTH_OBJ, "GET"))],
)
async def integration_health(
    service: IntegrationHealthService = Depends(provide_integration_health_service),
) -> ApiEnvelope[IntegrationHealthResponse]:
    results = await service.check_all()
    overall = "ok" if all(r.status != "error" for r in results) else "degraded"
    return ApiEnvelope(
        data=IntegrationHealthResponse(
            overall_status=overall,
            integrations=[_to_model(r) for r in results],
        )
    )
