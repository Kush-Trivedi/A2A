from .auth_route import auth_router, oauth_compact_router
from .capability_route import capability_router
from .chat_route import chat_router
from .documents_route import connections_router, documents_router
from .files_route import files_router
from .registry_route import onboarding_router, registry_router

__all__ = [
    "auth_router",
    "capability_router",
    "chat_router",
    "connections_router",
    "documents_router",
    "files_router",
    "oauth_compact_router",
    "onboarding_router",
    "registry_router",
]
