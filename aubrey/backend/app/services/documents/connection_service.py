"""Team-owned connection registry.

A connection records WHERE a team's data lives (storage account/container,
SharePoint site/drive) under a connection_key. Credentials are never stored:
the platform identity is granted access on the team's side (Storage Blob Data
Reader / Sites.Selected). Ingest requests reference team + connection_key
instead of carrying raw URLs.
"""

import uuid

from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.agents import OdtTeamEntity
from ...entity.documents import ConnectionEntity, ConnectionType
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError, NotFoundError, ValidationError

logger = Logger(__name__).get_logger()

_REQUIRED_CONFIG = {
    ConnectionType.BLOB: ("account_url", "container"),
    # The SharePoint host is tenant-wide and lives in the env yaml
    # (microsoft.sharepoint.hostname) — teams only differ by site + drive.
    ConnectionType.SHAREPOINT: ("site_path", "drive_name"),
    # Databricks: `workspace` names a key in the yaml databricks.workspaces
    # map (host+PAT are platform-held); the space/warehouse is team-owned.
    ConnectionType.GENIE: ("workspace", "space_id"),
    ConnectionType.DATABRICKS_SQL: ("workspace", "warehouse_id"),
    # Vendor MCP server: server_url required; optional auth_header_name
    # (default Authorization) + auth_header_value for the vendor credential.
    ConnectionType.MCP: ("server_url",),
}


class ConnectionService:
    def __init__(self) -> None:
        self._db = get_postgres_connector()

    async def register(
        self,
        *,
        context: SessionContext,
        team_key: str,
        connection_key: str,
        source_type: str,
        config: dict[str, str],
        description: str = "",
    ) -> ConnectionEntity:
        team = team_key.strip().lower()
        key = connection_key.strip().lower()
        if not key:
            raise ValidationError("Connection key must not be empty.")
        required = _REQUIRED_CONFIG.get(source_type)
        if required is None:
            raise ValidationError(
                f"Unknown source_type '{source_type}'. Use one of: "
                f"{', '.join(sorted(_REQUIRED_CONFIG))}."
            )
        cleaned = {k: str(v).strip() for k, v in config.items() if str(v).strip()}
        missing = [k for k in required if not cleaned.get(k)]
        if missing:
            raise ValidationError(
                f"Connection config for '{source_type}' is missing: {', '.join(missing)}.",
                details={"required": list(required)},
            )

        try:
            async with self._db.session() as session:
                team_row = (
                    await session.exec(
                        select(OdtTeamEntity).where(
                            OdtTeamEntity.tenant_id == context.tenant_id,
                            OdtTeamEntity.key == team,
                        )
                    )
                ).first()
                if team_row is None:
                    raise NotFoundError(
                        f"Team '{team}' is not registered.", details={"team_key": team}
                    )

                existing = (
                    await session.exec(
                        select(ConnectionEntity).where(
                            ConnectionEntity.tenant_id == context.tenant_id,
                            ConnectionEntity.team_key == team,
                            ConnectionEntity.connection_key == key,
                        )
                    )
                ).first()
                if existing is not None:
                    existing.source_type = source_type
                    existing.description = description
                    existing.config = cleaned
                    session.add(existing)
                    return existing
                connection = ConnectionEntity(
                    id=uuid.uuid4().hex,
                    tenant_id=context.tenant_id,
                    team_key=team,
                    connection_key=key,
                    source_type=source_type,
                    description=description,
                    config=cleaned,
                )
                session.add(connection)
                logger.info(
                    "Connection registered",
                    extra={"team_key": team, "connection_key": key, "source_type": source_type},
                )
                return connection
        except (NotFoundError, ValidationError):
            raise
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def list(
        self, *, context: SessionContext, team_key: str | None = None
    ) -> list[ConnectionEntity]:
        try:
            async with self._db.session() as session:
                query = select(ConnectionEntity).where(
                    ConnectionEntity.tenant_id == context.tenant_id
                )
                if team_key and team_key.strip():
                    query = query.where(
                        ConnectionEntity.team_key == team_key.strip().lower()
                    )
                rows = (
                    await session.exec(
                        query.order_by(ConnectionEntity.team_key, ConnectionEntity.connection_key)
                    )
                ).all()
                return list(rows)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def get(
        self, *, context: SessionContext, team_key: str, connection_key: str
    ) -> ConnectionEntity:
        team = team_key.strip().lower()
        key = connection_key.strip().lower()
        try:
            async with self._db.session() as session:
                connection = (
                    await session.exec(
                        select(ConnectionEntity).where(
                            ConnectionEntity.tenant_id == context.tenant_id,
                            ConnectionEntity.team_key == team,
                            ConnectionEntity.connection_key == key,
                        )
                    )
                ).first()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        if connection is None:
            raise NotFoundError(
                f"Connection '{key}' is not registered for team '{team}'. "
                "Register it via POST /api/v1/admin/connections.",
                details={"team_key": team, "connection_key": key},
            )
        return connection


_service: ConnectionService | None = None


def get_connection_service() -> ConnectionService:
    global _service
    if _service is None:
        _service = ConnectionService()
    return _service
