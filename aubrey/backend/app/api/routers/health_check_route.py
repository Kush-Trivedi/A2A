from fastapi import APIRouter

health_check_router = APIRouter(tags=["Health"])


@health_check_router.get("/api/healthcheck")
async def healthcheck() -> dict:
    return {"status": "ok"}
