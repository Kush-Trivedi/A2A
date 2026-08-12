from typing import Awaitable, Callable
from fastapi import Depends
from ...utils.common.logger import Logger
from ...utils.errors import PermissionDeniedError
from ..dependencies import get_current_context
from ..session import SessionContext
from .context_attrs import AuthorizationContextBuilder
from .enforcer import CasbinEnforcer, get_casbin_enforcer

logger = Logger(__name__).get_logger()

PermissionDependency = Callable[..., Awaitable[SessionContext]]


class PermissionGuard:
    def __init__(self, enforcer: CasbinEnforcer | None = None) -> None:
        self._enforcer = enforcer or get_casbin_enforcer()

    def require(self, obj: str, act: str) -> PermissionDependency:
        async def _dependency(
            context: SessionContext = Depends(get_current_context),
        ) -> SessionContext:
            if not self._enforcer.enabled:
                return context

            if not context.roles:
                logger.info(
                    "Authorization denied: no roles on session",
                    extra={"tenant_id": context.tenant_id, "user_id": context.user_id, "obj": obj, "act": act},
                )
                raise PermissionDeniedError(details={"obj": obj, "act": act})

            allowed = await self._enforcer.enforce_any_role(
                context.roles,
                context.tenant_id,
                obj,
                act,
                AuthorizationContextBuilder.build(context),
            )
            if not allowed:
                logger.info(
                    "Authorization denied by policy",
                    extra={
                        "tenant_id": context.tenant_id,
                        "user_id": context.user_id,
                        "roles": list(context.roles),
                        "obj": obj,
                        "act": act,
                        "attrs": AuthorizationContextBuilder.build(context),
                    },
                )
                raise PermissionDeniedError(details={"obj": obj, "act": act})
            return context

        return _dependency

_guard = PermissionGuard()

def require_permission(obj: str, act: str) -> PermissionDependency:
    return _guard.require(obj, act)