from fastapi import APIRouter

from .rest import auth_router, oauth_compact_router, onboarding_router, registry_router

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(auth_router)
v1_router.include_router(registry_router)
v1_router.include_router(onboarding_router)

__all__ = ["oauth_compact_router", "v1_router"]
