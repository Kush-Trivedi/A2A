from fastapi import APIRouter
from ...dto.common import HealthCheckResponse

health_check_router = APIRouter()

@health_check_router.get("/api/healthcheck", response_model=HealthCheckResponse, tags=["Health Check"])
async def health_check() -> HealthCheckResponse:
    return HealthCheckResponse(status="ok", message="Service is running")