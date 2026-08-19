from datetime import datetime, timedelta, timezone
from functools import lru_cache
from sqlalchemy import delete, update
from sqlmodel import col, select
from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.authz.browser_session_entity import BrowserSessionEntity
from ...utils.common.logger import Logger
from ..settings import AuthSettings, get_auth_settings
from .context import SessionContext
from .crypto import SessionCrypto

logger = Logger(__name__).get_logger()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SessionStore:
    def __init__(
        self,
        settings: AuthSettings | None = None,
        crypto: SessionCrypto | None = None,
    ) -> None:
        self._settings = settings or get_auth_settings()
        self._crypto = crypto or SessionCrypto(self._settings)
        self._db = get_postgres_connector()

    @property
    def crypto(self) -> SessionCrypto:
        return self._crypto

    async def create(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_id: str,
        email: str,
        display_name: str,
        roles: tuple[str, ...],
        auth_provider: str = "entra",
        ip: str = "",
        user_agent: str = "",
        user_profile: dict | None = None,
    ) -> SessionContext:
        session_id = self._crypto.new_session_id()
        csrf_token = self._crypto.new_csrf_token()
        ip_hash, ua_hash = self._crypto.fingerprint(ip, user_agent)

        now = _now()
        expires_at = now + timedelta(seconds=self._settings.session_ttl_seconds)
        profile = user_profile or {}

        entity = BrowserSessionEntity(
            session_id=self._crypto.hash(session_id),
            tenant_id=tenant_id,
            user_id=user_id,
            actor_id=actor_id,
            email=email,
            display_name=display_name,
            auth_provider=auth_provider,
            roles=list(roles),
            user_profile=profile,
            csrf_token_hash=self._crypto.hash(csrf_token),
            ip_hash=ip_hash,
            user_agent_hash=ua_hash,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
        )
        async with self._db.session() as session:
            session.add(entity)

        logger.info(
            "Session created",
            extra={"tenant_id": tenant_id, "user_id": user_id, "roles": list(roles)},
        )
        return SessionContext(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            actor_id=actor_id,
            email=email,
            display_name=display_name,
            auth_provider=auth_provider,
            csrf_token=csrf_token,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
            roles=tuple(roles),
            user_profile=profile,
        )

    async def get(
        self,
        session_id: str,
        *,
        ip: str = "",
        user_agent: str = "",
        verify_fingerprint: bool = True,
    ) -> SessionContext | None:
        async with self._db.session() as session:
            record = await session.get(BrowserSessionEntity, self._crypto.hash(session_id))
            if record is None:
                return None

            if record.expires_at <= _now():
                await session.delete(record)
                logger.info("Session expired", extra={"session_id_hash": self._crypto.hash(session_id)[:16]})
                return None

            if verify_fingerprint and not self._fingerprint_ok(record, ip, user_agent):
                await session.delete(record)
                logger.warning(
                    "Session fingerprint mismatch — invalidating",
                    extra={"session_id_hash": self._crypto.hash(session_id)[:16]},
                )
                return None

            return self._to_context(record, raw_session_id=session_id)

    async def touch(self, session_id: str) -> None:
        stmt = (
            update(BrowserSessionEntity)
            .where(col(BrowserSessionEntity.session_id) == self._crypto.hash(session_id))
            .values(last_seen_at=_now())
        )
        async with self._db.session() as session:
            await session.exec(stmt)

    async def delete(self, session_id: str) -> None:
        stmt = delete(BrowserSessionEntity).where(
            col(BrowserSessionEntity.session_id) == self._crypto.hash(session_id)
        )
        async with self._db.session() as session:
            await session.exec(stmt)

    async def delete_for_user(self, tenant_id: str, user_id: str) -> int:
        async with self._db.session() as session:
            result = await session.exec(
                select(BrowserSessionEntity.session_id).where(
                    BrowserSessionEntity.tenant_id == tenant_id,
                    BrowserSessionEntity.user_id == user_id,
                )
            )
            ids = list(result.all())
            if ids:
                await session.exec(
                    delete(BrowserSessionEntity).where(
                        col(BrowserSessionEntity.session_id).in_(ids)
                    )
                )
            return len(ids)

    async def purge_expired(self) -> int:
        async with self._db.session() as session:
            result = await session.exec(
                select(BrowserSessionEntity.session_id).where(
                    col(BrowserSessionEntity.expires_at) <= _now()
                )
            )
            ids = list(result.all())
            if ids:
                await session.exec(
                    delete(BrowserSessionEntity).where(
                        col(BrowserSessionEntity.session_id).in_(ids)
                    )
                )
            return len(ids)

    def _fingerprint_ok(
        self, record: BrowserSessionEntity, ip: str, user_agent: str
    ) -> bool:
        ip_hash, ua_hash = self._crypto.fingerprint(ip, user_agent)
        return self._crypto.verify_hash(
            ip_hash, record.ip_hash
        ) and self._crypto.verify_hash(ua_hash, record.user_agent_hash)

    def _to_context(
        self, record: BrowserSessionEntity, raw_session_id: str | None = None
    ) -> SessionContext:
        # PK is the HASH of the cookie value (a DB read must never yield a
        # usable session id); the context carries the raw value when known.
        return SessionContext(
            session_id=raw_session_id or record.session_id,
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            actor_id=record.actor_id,
            email=record.email,
            display_name=record.display_name,
            auth_provider=record.auth_provider,
            csrf_token="",
            created_at=record.created_at,
            last_seen_at=record.last_seen_at,
            expires_at=record.expires_at,
            roles=tuple(record.roles or ()),
            user_profile=dict(record.user_profile or {}),
        )


    async def verify_csrf(self, session_id: str, csrf_token: str) -> bool:
        async with self._db.session() as session:
            record = await session.get(BrowserSessionEntity, session_id)
            if record is None:
                return False
            return self._crypto.verify(csrf_token, record.csrf_token_hash)


@lru_cache(maxsize=1)
def get_session_store() -> SessionStore:
    return SessionStore()
