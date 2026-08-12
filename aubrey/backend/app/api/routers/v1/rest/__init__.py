from .auth_route import auth_router, oauth_compact_router
from .documents_route import connections_router, documents_router
from .registry_route import onboarding_router, registry_router

__all__ = [
    "auth_router",
    "connections_router",
    "documents_router",
    "oauth_compact_router",
    "onboarding_router",
    "registry_router",
]
