from .health_check_route import health_check_router
from .v1 import v1_router
from .v1.auth_v1_routes import oauth_compact_router

__all__ = ["health_check_router", "oauth_compact_router", "v1_router"]
