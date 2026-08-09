from fastapi import APIRouter

from .agents_v1_routes import agent_registry_v1_router
from .capability_v1_routes import capability_v1_router
from .channels_v1_routes import sms_channel_v1_router, teams_channel_v1_router
from .auth_v1_routes import auth_v1_router
from .authz_v1_routes import admin_v1_router
from .chat_v1_routes import chat_v1_router
from .embedding_v1_routes import embedding_v1_router, ingestion_v1_router
from .health_v1_routes import admin_health_v1_router

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(auth_v1_router)
v1_router.include_router(admin_v1_router)
v1_router.include_router(agent_registry_v1_router)
v1_router.include_router(admin_health_v1_router)
v1_router.include_router(capability_v1_router)
v1_router.include_router(sms_channel_v1_router)
v1_router.include_router(teams_channel_v1_router)
v1_router.include_router(chat_v1_router)
v1_router.include_router(embedding_v1_router)
v1_router.include_router(ingestion_v1_router)

__all__ = ["v1_router"]