from dataclasses import dataclass
from functools import lru_cache
from ...database.rdbms.pg_session import get_postgres_connector
from ...security.identity.jwt_validator import ValidatedIdentity
from ...utils.common.logger import Logger
from .identity_attributes import IdentityAuthorizationAttributes
from .role_resolution_service import ResolvedRoles, RoleResolutionService
from .user_provisioning_service import ProvisionedUser, UserProvisioningService

logger = Logger(__name__).get_logger()

@dataclass(frozen=True)
class LoginProvisioning:
    tenant_id: str
    user_id: str
    actor_id: str
    email: str
    display_name: str
    roles: tuple[str, ...]
    is_new_login: bool
    granted_roles: tuple[str, ...]
    revoked_roles: tuple[str, ...]
    granted_groups: tuple[str, ...]
    revoked_groups: tuple[str, ...]
    authorization_attributes: dict[str, str | int | float | bool]


class AuthzLoginService:
    def __init__(
        self,
        resolver: RoleResolutionService | None = None,
        provisioner: UserProvisioningService | None = None,
        attributes: IdentityAuthorizationAttributes | None = None,
    ) -> None:
        self._resolver = resolver or RoleResolutionService()
        self._provisioner = provisioner or UserProvisioningService()
        self._attributes = attributes or IdentityAuthorizationAttributes()
        self._db = get_postgres_connector()

    async def provision_on_login(self, identity: ValidatedIdentity) -> LoginProvisioning:
        async with self._db.session() as session:
            resolved: ResolvedRoles = await self._resolver.resolve(session, identity)
            roles = resolved.roles

            if not roles:
                logger.warning(
                    "Authenticated Entra identity has no mapped roles; access will be denied by default.",
                    extra={
                        "tenant_id": identity.tenant_id,
                        "actor_id": identity.actor_id,
                    },
                )

            provisioned: ProvisionedUser = await self._provisioner.provision(
                session, identity, roles
            )

            await session.commit()

        return LoginProvisioning(
            tenant_id=identity.tenant_id,
            user_id=provisioned.user_id,
            actor_id=identity.actor_id,
            email=identity.email,
            display_name=identity.display_name,
            roles=roles,
            is_new_login=provisioned.is_new_login,
            granted_roles=provisioned.granted_roles,
            revoked_roles=provisioned.revoked_roles,
            granted_groups=provisioned.granted_groups,
            revoked_groups=provisioned.revoked_groups,
            authorization_attributes=self._attributes.project(identity),
        )
    
@lru_cache(maxsize=1)
def get_authz_login_service() -> AuthzLoginService:
    return AuthzLoginService()
