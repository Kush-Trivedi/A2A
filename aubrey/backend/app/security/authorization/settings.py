from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from ...config.application_context import get_application_context

@dataclass
class AuthorizationSettings:
    rbac_enabled: bool
    model_path: str
    full_access_roles: tuple[str, ...] = ()
    policy_reload_seconds: int = 0

def _as_roles(raw: object) -> tuple[str, ...]:
    if isinstance(raw, (list, tuple)):
        items = [str(role).strip() for role in raw]
    else:
        items = [role.strip() for role in str(raw or "").split(",")]
    return tuple(dict.fromkeys(role for role in items if role))

@lru_cache()
def get_authorization_settings() -> AuthorizationSettings:
    authz = get_application_context().authorization
    default_model_path = str(Path(__file__).parent / "casbin_model.conf")
    return AuthorizationSettings(
        rbac_enabled=bool(authz.get("rbac_enabled", True)),
        model_path=str(authz.get("rbac_model_path") or default_model_path),
        full_access_roles=_as_roles(authz.get("full_access_roles") or "developer"),
        policy_reload_seconds=int(authz.get("policy_reload_seconds") or 0),
    )
