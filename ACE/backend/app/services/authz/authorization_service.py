from functools import lru_cache
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import col, desc, select

from ...database.rdbms.pg_session import get_postgres_connector
from ...dto.authz.policy import (
    PolicyAuditEntry,
    PolicyTuple,
    RoleMappingRequest,
    RoleMappingResponse,
)
from ...entity.authz.entra_role_mapping_entity import EntraRoleMappingEntity
from ...entity.authz.policy_audit_log_entity import PolicyAuditLogEntity
from ...security.authorization.enforcer import CasbinEnforcer, get_casbin_enforcer
from ...security.session import SessionContext
from ...utils.common.logger import Logger

logger = Logger(__name__).get_logger()


class AuthorizationService:
    def __init__(self, enforcer: CasbinEnforcer | None = None) -> None:
        self._enforcer = enforcer or get_casbin_enforcer()
        self._db = get_postgres_connector()

    async def list_policies(self) -> list[PolicyTuple]:
        policies: list[PolicyTuple] = []
        for r in self._enforcer.list_policies():
            effect = r[4] if len(r) > 4 and r[4] else "allow"
            attrs = r[5] if len(r) > 5 and r[5] else "*"
            policies.append(
                PolicyTuple(
                    role=r[0],
                    domain=r[1],
                    resource=r[2],
                    action=r[3],
                    effect=effect,
                    attrs=attrs,
                )
            )
        return policies

    async def add_policy(self, *, policy: PolicyTuple, actor: SessionContext) -> bool:
        added = await self._enforcer.add_policy(
            policy.role,
            policy.domain,
            policy.resource,
            policy.action,
            policy.effect,
            policy.attrs,
        )
        await self._audit(actor=actor, action="add_policy", policy=policy)
        return added

    async def remove_policy(self, *, policy: PolicyTuple, actor: SessionContext) -> bool:
        removed = await self._enforcer.remove_policy(
            policy.role,
            policy.domain,
            policy.resource,
            policy.action,
            policy.effect,
            policy.attrs,
        )
        await self._audit(actor=actor, action="remove_policy", policy=policy)
        return removed

    async def reload_policies(self, *, actor: SessionContext) -> int:
        count = await self._enforcer.reload()
        await self._audit(actor=actor, action="reload_policies")
        return count

    async def list_audit(self, *, limit: int = 100) -> list[PolicyAuditEntry]:
        async with self._db.session() as session:
            result = await session.exec(
                select(PolicyAuditLogEntity)
                .order_by(desc(PolicyAuditLogEntity.created_at))
                .limit(limit)
            )
            rows = result.all()
        return [
            PolicyAuditEntry(
                created_at=row.created_at,
                actor_id=row.actor_id,
                tenant_id=row.tenant_id,
                action=row.action,
                target_role=row.target_role,
                target_domain=row.target_domain,
                target_resource=row.target_resource,
                target_action=row.target_action,
            )
            for row in rows
        ]

    async def list_role_mappings(self, *, tenant_id: str) -> list[RoleMappingResponse]:
        async with self._db.session() as session:
            result = await session.exec(
                select(EntraRoleMappingEntity).where(
                    EntraRoleMappingEntity.tenant_id == tenant_id
                )
            )
            rows = result.all()
        return [self._to_mapping_dto(r) for r in rows]

    async def upsert_role_mapping(
        self, *, request: RoleMappingRequest, actor: SessionContext
    ) -> RoleMappingResponse:
        async with self._db.session() as session:
            stmt = (
                pg_insert(EntraRoleMappingEntity)
                .values(
                    id=uuid4().hex,
                    tenant_id=request.tenant_id,
                    claim_type=request.claim_type,
                    claim_value=request.claim_value,
                    role_key=request.role_key,
                    enabled=request.enabled,
                    description=request.description,
                )

                .on_conflict_do_update(
                    index_elements=["tenant_id", "claim_type", "claim_value", "role_key"],
                    set_={
                        "enabled": request.enabled,
                        "description": request.description,
                    },
                )
                .returning(EntraRoleMappingEntity)
            )
            result = await session.exec(stmt)
            row = result.one()
        logger.info(
            "Role mapping upserted",
            extra={
                "actor_id": actor.actor_id,
                "tenant_id": request.tenant_id,
                "claim_type": request.claim_type,
                "role_key": request.role_key,
            },
        )
        return self._to_mapping_dto(row)

    async def delete_role_mapping(self, *, mapping_id: str, actor: SessionContext) -> bool:
        async with self._db.session() as session:
            result = await session.exec(
                delete(EntraRoleMappingEntity).where(
                    col(EntraRoleMappingEntity.id) == mapping_id
                )
            )
            deleted = bool(result.rowcount)
        logger.info(
            "Role mapping deleted",
            extra={"actor_id": actor.actor_id, "mapping_id": mapping_id, "deleted": deleted},
        )
        return deleted


    async def my_policies(self, *, context: SessionContext) -> list[PolicyTuple]:
        all_policies = await self.list_policies()
        roles = set(context.roles)
        return [
            p
            for p in all_policies
            if p.role in roles and p.domain in (context.tenant_id, "*")
        ]

    async def _audit(
        self,
        *,
        actor: SessionContext,
        action: str,
        policy: PolicyTuple | None = None,
    ) -> None:
        entry = PolicyAuditLogEntity(
            actor_id=actor.actor_id,
            tenant_id=actor.tenant_id,
            action=action,
            target_role=policy.role if policy else "-",
            target_domain=policy.domain if policy else "-",
            target_resource=policy.resource if policy else "-",
            target_action=policy.action if policy else "-",
        )
        async with self._db.session() as session:
            session.add(entry)

    @staticmethod
    def _to_mapping_dto(row: EntraRoleMappingEntity) -> RoleMappingResponse:
        return RoleMappingResponse(
            id=row.id,
            tenant_id=row.tenant_id,
            claim_type=row.claim_type,
            claim_value=row.claim_value,
            role_key=row.role_key,
            enabled=row.enabled,
            description=row.description,
        )

@lru_cache(maxsize=1)
def get_authorization_service() -> AuthorizationService:
    return AuthorizationService()
