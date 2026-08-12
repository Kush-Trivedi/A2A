from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import and_, delete, func, literal_column
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession
from ...entity.authz.user_entity import UserEntity
from ...entity.authz.user_entra_group_assignment_entity import (
    UserEntraGroupAssignmentEntity,
)
from ...entity.authz.user_role_assignment_entity import UserRoleAssignmentEntity
from ...security.jwt_validator import ValidatedIdentity
from ...utils.common.logger import Logger

logger = Logger(__name__).get_logger()


SOURCE_ENTRA = "entra"
SOURCE_ENTRA_ID_TOKEN = "entra_id_token"

@dataclass(frozen=True)
class ProvisionedUser:
    user_id: str
    tenant_id: str
    is_new_login: bool
    granted_roles: tuple[str, ...]
    revoked_roles: tuple[str, ...]
    granted_groups: tuple[str, ...]
    revoked_groups: tuple[str, ...]


class UserProvisioningService:
    async def provision(
        self,
        session: AsyncSession,
        identity: ValidatedIdentity,
        resolved_roles: tuple[str, ...],
    ) -> ProvisionedUser:
        user_id, is_new = await self._upsert_user(session, identity)
        granted, revoked = await self._sync_role_assignments(
            session, identity.tenant_id, user_id, resolved_roles
        )
        granted_groups, revoked_groups = await self._sync_group_assignments(
            session, identity, user_id
        )

        logger.info(
            "JIT provisioning complete",
            extra={
                "tenant_id": identity.tenant_id,
                "user_id": user_id,
                "is_new_login": is_new,
                "granted_roles": list(granted),
                "revoked_roles": list(revoked),
                "granted_group_count": len(granted_groups),
                "revoked_group_count": len(revoked_groups),
            },
        )
        return ProvisionedUser(
            user_id=user_id,
            tenant_id=identity.tenant_id,
            is_new_login=is_new,
            granted_roles=granted,
            revoked_roles=revoked,
            granted_groups=granted_groups,
            revoked_groups=revoked_groups,
        )

    async def _sync_group_assignments(
        self,
        session: AsyncSession,
        identity: ValidatedIdentity,
        user_id: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if "groups" not in identity.raw_claims and not identity.has_group_overage:
            return (), ()
        if identity.has_group_overage:
            logger.warning(
                "Entra group overage detected; preserving stored memberships until Graph resolution is available.",
                extra={
                    "tenant_id": identity.tenant_id,
                    "user_id": user_id,
                },
            )
            return (), ()

        desired = {group_id for group_id in identity.groups if group_id}
        existing = await self._existing_entra_groups(
            session, identity.tenant_id, user_id
        )
        to_grant = desired - existing
        to_revoke = existing - desired

        if to_grant:
            rows = [
                {
                    "id": uuid4().hex,
                    "tenant_id": identity.tenant_id,
                    "user_id": user_id,
                    "group_id": group_id,
                    "source": SOURCE_ENTRA_ID_TOKEN,
                    "created_at": datetime.now(timezone.utc),
                }
                for group_id in to_grant
            ]
            stmt = (
                pg_insert(UserEntraGroupAssignmentEntity)
                .values(rows)
                .on_conflict_do_nothing(
                    constraint="uq_user_entra_group",
                )
            )
            await session.exec(stmt)

        if to_revoke:
            stmt = delete(UserEntraGroupAssignmentEntity).where(
                and_(
                    UserEntraGroupAssignmentEntity.tenant_id == identity.tenant_id,
                    UserEntraGroupAssignmentEntity.user_id == user_id,
                    UserEntraGroupAssignmentEntity.source == SOURCE_ENTRA_ID_TOKEN,
                    col(UserEntraGroupAssignmentEntity.group_id).in_(to_revoke),
                )
            )
            await session.exec(stmt)

        return tuple(sorted(to_grant)), tuple(sorted(to_revoke))

    async def _existing_entra_groups(
        self,
        session: AsyncSession,
        tenant_id: str,
        user_id: str,
    ) -> set[str]:
        from sqlmodel import select

        statement = select(UserEntraGroupAssignmentEntity.group_id).where(
            UserEntraGroupAssignmentEntity.tenant_id == tenant_id,
            UserEntraGroupAssignmentEntity.user_id == user_id,
            UserEntraGroupAssignmentEntity.source == SOURCE_ENTRA_ID_TOKEN,
        )
        result = await session.exec(statement)
        return set(result.all())

    async def _upsert_user(
        self, session: AsyncSession, identity: ValidatedIdentity
    ) -> tuple[str, bool]:
        now = datetime.now(timezone.utc)
        insert_stmt = pg_insert(UserEntity).values(
            id=uuid4().hex,
            tenant_id=identity.tenant_id,
            external_subject_id=identity.actor_id,
            email=identity.email,
            first_name=identity.first_name,
            last_name=identity.last_name,
            auth_provider=SOURCE_ENTRA,
            status="active",
            last_login_at=now,
            created_at=now,
            updated_at=now,
        )
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=["tenant_id", "external_subject_id"],
            set_={
                "email": func.coalesce(
                    func.nullif(insert_stmt.excluded.email, ""), UserEntity.email
                ),
                "first_name": func.coalesce(
                    func.nullif(insert_stmt.excluded.first_name, ""), UserEntity.first_name
                ),
                "last_name": func.coalesce(
                    func.nullif(insert_stmt.excluded.last_name, ""), UserEntity.last_name
                ),
                "status": "active",
                "last_login_at": now,
                "updated_at": now,
            },
        ).returning(
            UserEntity.id,
            literal_column("(xmax = 0)").label("was_inserted"),
        )

        result = await session.exec(upsert_stmt)
        row = result.one()
        user_id = row[0]
        is_new = bool(row[1])
        return user_id, is_new

    async def _sync_role_assignments(
        self,
        session: AsyncSession,
        tenant_id: str,
        user_id: str,
        resolved_roles: tuple[str, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        desired = set(resolved_roles)
        existing = await self._existing_entra_roles(session, tenant_id, user_id)
        to_grant = desired - existing
        to_revoke = existing - desired

        if to_grant:
            await self._insert_assignments(session, tenant_id, user_id, to_grant)

        if to_revoke:
            await self._revoke_assignments(session, tenant_id, user_id, to_revoke)

        return tuple(sorted(to_grant)), tuple(sorted(to_revoke))

    async def _existing_entra_roles(
        self, session: AsyncSession, tenant_id: str, user_id: str
    ) -> set[str]:
        from sqlmodel import select

        statement = select(UserRoleAssignmentEntity.role).where(
            UserRoleAssignmentEntity.tenant_id == tenant_id,
            UserRoleAssignmentEntity.user_id == user_id,
            UserRoleAssignmentEntity.source == SOURCE_ENTRA,
        )
        result = await session.exec(statement)
        return set(result.all())

    async def _insert_assignments(
        self,
        session: AsyncSession,
        tenant_id: str,
        user_id: str,
        roles: set[str],
    ) -> None:
        rows = [
            {
                "id": uuid4().hex,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "role": role,
                "source": SOURCE_ENTRA,
                "created_at": datetime.now(timezone.utc),
            }
            for role in roles
        ]
        stmt = pg_insert(UserRoleAssignmentEntity).values(rows).on_conflict_do_nothing(
            index_elements=["tenant_id", "user_id", "role"],
        )
        await session.exec(stmt)

    async def _revoke_assignments(
        self,
        session: AsyncSession,
        tenant_id: str,
        user_id: str,
        roles: set[str],
    ) -> None:
        stmt = delete(UserRoleAssignmentEntity).where(
            and_(
                UserRoleAssignmentEntity.tenant_id == tenant_id,
                UserRoleAssignmentEntity.user_id == user_id,
                UserRoleAssignmentEntity.source == SOURCE_ENTRA,
                col(UserRoleAssignmentEntity.role).in_(roles),
            )
        )
        await session.exec(stmt)
