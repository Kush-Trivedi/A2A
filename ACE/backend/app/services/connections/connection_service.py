import uuid
from datetime import datetime, timezone

from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.agents import OdtTeamEntity
from ...entity.connections import (
    CONNECTION_STATUS_ACTIVE,
    TeamConnectionEntity,
)
from ...security.field_encryptor import FieldEncryptor, get_field_encryptor
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError, NotFoundError, ValidationError

logger = Logger(__name__).get_logger()


class ConnectionService:
    """Team-owned integration configuration, referenced by name.

    ACE provides the mechanism (storage, encryption at rest, ownership,
    health); teams provide the values. Nothing here ever reads ACE yaml —
    a new team integration is an API call, never a platform deploy.
    """

    # Per-type minimum configuration: (required config keys, required secret
    # keys). The health probe reports exactly which key is missing — same
    # discipline as the settings validator: findings name the fix.
    _TYPE_REQUIREMENTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "sharepoint": (("tenant_id", "client_id", "hostname"), ("client_secret",)),
        "storage_blob": (("account_url",), ()),
        "databricks": (("host",), ("token",)),
        "twilio": (("account_sid",), ("auth_token",)),
    }

    def __init__(self, encryptor: FieldEncryptor | None = None) -> None:
        self._connector = get_postgres_connector()
        self._crypto = encryptor or get_field_encryptor()

    async def register_connection(
        self,
        *,
        context: SessionContext,
        team_key: str,
        name: str,
        connection_type: str,
        description: str = "",
        config: dict | None = None,
        secrets: dict | None = None,
    ) -> TeamConnectionEntity:
        """Create or update (upsert by tenant+name). Only the owning team may
        update an existing connection; secret values are encrypted at rest."""
        team_key = team_key.strip().lower()
        if not team_key:
            raise ValidationError("team_key must not be empty.")

        encrypted_secrets = {
            str(key): (self._crypto.encrypt(str(value)) or str(value))
            for key, value in (secrets or {}).items()
            if str(value).strip()
        }

        try:
            async with self._connector.session() as session:
                team = (
                    await session.exec(
                        select(OdtTeamEntity).where(
                            OdtTeamEntity.tenant_id == context.tenant_id,
                            OdtTeamEntity.key == team_key,
                        )
                    )
                ).first()
                if team is None:
                    raise NotFoundError(
                        "Team is not registered. Register the team before its connections.",
                        details={"team_key": team_key},
                    )

                existing = (
                    await session.exec(
                        select(TeamConnectionEntity).where(
                            TeamConnectionEntity.tenant_id == context.tenant_id,
                            TeamConnectionEntity.name == name,
                        )
                    )
                ).first()

                if existing is not None:
                    if existing.team_key != team_key:
                        raise ValidationError(
                            "Connection name is owned by another team.",
                            details={"name": name, "owner_team": existing.team_key},
                        )
                    existing.connection_type = connection_type
                    existing.description = description
                    existing.config = dict(config or {})
                    # Merge secrets: omitted keys keep their stored value, so
                    # teams can update config without re-sending credentials.
                    existing.secrets = {**dict(existing.secrets or {}), **encrypted_secrets}
                    existing.updated_at = datetime.now(timezone.utc)
                    session.add(existing)
                    logger.info(
                        "Connection updated",
                        extra={"name": name, "team_key": team_key, "type": connection_type},
                    )
                    return existing

                connection = TeamConnectionEntity(
                    id=uuid.uuid4().hex,
                    tenant_id=context.tenant_id,
                    team_key=team_key,
                    name=name,
                    connection_type=connection_type,
                    description=description,
                    status=CONNECTION_STATUS_ACTIVE,
                    config=dict(config or {}),
                    secrets=encrypted_secrets,
                )
                session.add(connection)
                logger.info(
                    "Connection registered",
                    extra={"name": name, "team_key": team_key, "type": connection_type},
                )
                return connection
        except (ValidationError, NotFoundError):
            raise
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def list_connections(
        self, *, context: SessionContext, team_key: str | None = None
    ) -> list[TeamConnectionEntity]:
        try:
            async with self._connector.session() as session:
                statement = select(TeamConnectionEntity).where(
                    TeamConnectionEntity.tenant_id == context.tenant_id
                )
                if team_key:
                    statement = statement.where(
                        TeamConnectionEntity.team_key == team_key.strip().lower()
                    )
                rows = (await session.exec(statement.order_by(TeamConnectionEntity.name))).all()
                return list(rows)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def get_connection(
        self, *, tenant_id: str, name: str
    ) -> TeamConnectionEntity | None:
        try:
            async with self._connector.session() as session:
                return (
                    await session.exec(
                        select(TeamConnectionEntity).where(
                            TeamConnectionEntity.tenant_id == tenant_id,
                            TeamConnectionEntity.name == name,
                        )
                    )
                ).first()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def resolve_config(self, *, tenant_id: str, name: str) -> dict:
        """Full decrypted settings for INTERNAL consumers (ingestion, channel
        services). Never exposed over HTTP."""
        connection = await self.get_connection(tenant_id=tenant_id, name=name)
        if connection is None:
            raise NotFoundError(
                "Connection is not registered.", details={"connection": name}
            )
        resolved = dict(connection.config or {})
        for key, value in (connection.secrets or {}).items():
            resolved[key] = self._crypto.decrypt(str(value))
        resolved["connection_type"] = connection.connection_type
        resolved["team_key"] = connection.team_key
        return resolved

    async def health(
        self, *, context: SessionContext, name: str
    ) -> tuple[TeamConnectionEntity, str, str]:
        """(connection, status, detail) — never raises past not-found.

        Reports configuration completeness per type; connectivity is proven on
        first real use (ingestion/send), matching the integration-health
        philosophy: ok / error / not_configured with the exact key to fix.
        """
        connection = await self.get_connection(tenant_id=context.tenant_id, name=name)
        if connection is None:
            raise NotFoundError(
                "Connection is not registered.", details={"connection": name}
            )

        required_config, required_secrets = self._TYPE_REQUIREMENTS.get(
            connection.connection_type, ((), ())
        )
        config = connection.config or {}
        secrets = connection.secrets or {}

        missing = [key for key in required_config if not str(config.get(key, "")).strip()]
        missing += [f"secrets.{key}" for key in required_secrets if not str(secrets.get(key, "")).strip()]
        if missing:
            return connection, "not_configured", f"Missing: {', '.join(missing)}"
        if connection.status != CONNECTION_STATUS_ACTIVE:
            return connection, "error", f"Connection status is '{connection.status}'."
        return connection, "ok", "Configured. Connectivity is verified on first use."


_service: ConnectionService | None = None


def get_connection_service() -> ConnectionService:
    global _service
    if _service is None:
        _service = ConnectionService()
    return _service
