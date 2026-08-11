import uuid
from dataclasses import dataclass, field
from sqlalchemy import and_, or_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from backend.app.entity.authz import (
    EntraClaimType,
    EntraRoleMappingEntity,
    RoleEntity
)
from ...security.jwt_validator import ValidatedIdentity
from ...utils.common.logger import Logger

logger = Logger(__name__).get_logger()

GLOBAL_TENANT = "*"
ENTRA_MAPPING_DESCRIPTION = "Verified Entra app-role mapping synchronized on login."


@dataclass(frozen=True)
class ResolvedRoles:
    tenant_id: str
    roles: tuple[str, ...] = ()
    matched_app_roles: tuple[str, ...] = ()
    matched_groups: tuple[str, ...] = ()
    unmatched_app_roles: tuple[str, ...] = ()
    unmatched_groups: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)


class RoleResolutionService:
    async def resolve(
        self,
        session: AsyncSession,
        identity: ValidatedIdentity,
    ) -> ResolvedRoles:
        app_roles = tuple(dict.fromkeys(identity.app_roles))
        groups = tuple(dict.fromkeys(identity.groups))

        if not app_roles and not groups:
            return ResolvedRoles(tenant_id=identity.tenant_id)

        await self._synchronize_app_roles(session, identity.tenant_id, app_roles)

        rows = await self._fetch_mappings(session, identity.tenant_id, app_roles, groups)
        roles: set[str] = set()
        matched_app: set[str] = set()
        matched_grp: set[str] = set()

        for claim_type, claim_value, role_key in rows:
            roles.add(role_key)
            if claim_type == EntraClaimType.APP_ROLE:
                matched_app.add(claim_value)
            elif claim_type == EntraClaimType.GROUP:
                matched_grp.add(claim_value)

        return ResolvedRoles(
            tenant_id=identity.tenant_id,
            roles=tuple(sorted(roles)),
            matched_app_roles=tuple(sorted(matched_app)),
            matched_groups=tuple(sorted(matched_grp)),
            unmatched_app_roles=tuple(r for r in app_roles if r not in matched_app),
            unmatched_groups=tuple(g for g in groups if g not in matched_grp),
        )

    async def _synchronize_app_roles(
        self,
        session: AsyncSession,
        tenant_id: str,
        app_roles: tuple[str, ...],
    ) -> None:
        for app_role in app_roles:
            role_stmt = pg_insert(RoleEntity).values(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                key=app_role,
                name=f"Entra App Role: {app_role}",
                description="Auto-discovered and registered during user authentication.",
                is_system=True
            ).on_conflict_do_nothing(constraint="uq_roles_tenant_key")
            await session.exec(role_stmt)

            mapping_stmt = pg_insert(EntraRoleMappingEntity).values(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                claim_type=EntraClaimType.APP_ROLE,
                claim_value=app_role,
                role_key=app_role,
                enabled=True,
                description=ENTRA_MAPPING_DESCRIPTION,
            ).on_conflict_do_nothing(
                index_elements=["tenant_id", "claim_type", "claim_value", "role_key"]
            )
            await session.exec(mapping_stmt)

        await session.flush()

    async def _fetch_mappings(
        self,
        session: AsyncSession,
        tenant_id: str,
        app_roles: tuple[str, ...],
        groups: tuple[str, ...],
    ) -> list[tuple[str, str, str]]:
        claim_predicates = []
        if app_roles:
            claim_predicates.append(
                and_(
                    EntraRoleMappingEntity.claim_type == EntraClaimType.APP_ROLE,
                    col(EntraRoleMappingEntity.claim_value).in_(app_roles),
                )
            )
        if groups:
            claim_predicates.append(
                and_(
                    EntraRoleMappingEntity.claim_type == EntraClaimType.GROUP,
                    col(EntraRoleMappingEntity.claim_value).in_(groups),
                )
            )

        if not claim_predicates:
            return []
        
        statement = select(
            EntraRoleMappingEntity.claim_type,
            EntraRoleMappingEntity.claim_value,
            EntraRoleMappingEntity.role_key,
        ).where(
            and_(
                EntraRoleMappingEntity.enabled == True,
                or_(*claim_predicates),
                or_(
                    EntraRoleMappingEntity.tenant_id == tenant_id,
                    EntraRoleMappingEntity.tenant_id == GLOBAL_TENANT,
                ),
            )
        )

        result = await session.exec(statement)
        return [[row.claim_type, row.claim_value, row.role_key] for row in result]
