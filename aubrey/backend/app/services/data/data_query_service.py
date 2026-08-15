"""The data plane — natural-language (Genie) and direct-SQL answers from
team-owned Databricks connections.

Everything a team can vary lives in its connection row (workspace key,
space/warehouse, catalog, schema); everything operational lives in yaml
(poll cadence, waits, row caps). The platform ships zero SQL and zero
data vocabulary.

Latency posture: Genie polls with 1s→5s backoff up to max_wait_seconds;
direct SQL uses the Statement Execution API's synchronous mode
(wait_timeout, inline results). Serverless warehouses are the strong
recommendation — 2-6s start vs ~4 minutes classic cold start, which is
the usual culprit behind minute-long answers."""

import asyncio
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from sqlmodel import select

from ...config.application_context import get_application_context
from ...database.databricks import DatabricksRestClient, get_workspace_client_factory
from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.data import GenieConversationEntity
from ...entity.documents import ConnectionEntity, ConnectionType
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError, ExternalServiceError, NotFoundError, ValidationError

logger = Logger(__name__).get_logger()

_TERMINAL_OK = {"COMPLETED"}
_TERMINAL_BAD = {"FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"}


@dataclass(frozen=True)
class DataSettings:
    poll_initial_seconds: float
    poll_max_seconds: float
    max_wait_seconds: float
    statement_wait_timeout: str
    max_result_rows: int


@lru_cache(maxsize=1)
def get_data_settings() -> DataSettings:
    data = get_application_context().databricks.get("data") or {}
    return DataSettings(
        poll_initial_seconds=float(data.get("poll_initial_seconds") or 1.0),
        poll_max_seconds=float(data.get("poll_max_seconds") or 5.0),
        max_wait_seconds=float(data.get("max_wait_seconds") or 90),
        statement_wait_timeout=str(data.get("statement_wait_timeout") or "50s"),
        max_result_rows=int(data.get("max_result_rows") or 100),
    )


@dataclass(frozen=True)
class DataAnswer:
    text: str = ""                      # Genie's own narration, when present
    sql: str = ""                       # the generated/executed statement
    columns: tuple[str, ...] = ()
    rows: tuple[tuple, ...] = ()
    row_count: int = 0
    truncated: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)


class DataQueryService:
    def __init__(self) -> None:
        self._db = get_postgres_connector()
        self._settings = get_data_settings()
        self._factory = get_workspace_client_factory()

    # ------------------------------------------------------------------ #
    # Connection resolution — always scoped to the calling team           #
    # ------------------------------------------------------------------ #

    async def resolve_connection(
        self, *, tenant_id: str, team_key: str, connection_key: str, expected_type: str
    ) -> ConnectionEntity:
        key = (connection_key or "").strip().lower()
        try:
            async with self._db.session() as session:
                connection = (
                    await session.exec(
                        select(ConnectionEntity).where(
                            ConnectionEntity.tenant_id == tenant_id,
                            ConnectionEntity.team_key == team_key,
                            ConnectionEntity.connection_key == key,
                        )
                    )
                ).first()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        if connection is None:
            raise NotFoundError(
                f"Connection '{key}' is not registered for team '{team_key}'.",
                details={"connection_key": key, "team_key": team_key},
            )
        if connection.source_type != expected_type:
            raise ValidationError(
                f"Connection '{key}' is '{connection.source_type}', expected "
                f"'{expected_type}'.",
            )
        return connection

    # ------------------------------------------------------------------ #
    # Genie — natural language                                            #
    # ------------------------------------------------------------------ #

    async def ask_genie(
        self,
        *,
        tenant_id: str,
        session_id: str,
        connection: ConnectionEntity,
        question: str,
    ) -> DataAnswer:
        config = dict(connection.config or {})
        space_id = str(config.get("space_id") or "")
        client = self._factory.get_client(str(config.get("workspace") or ""))

        conversation_id = await self._conversation_for(
            tenant_id=tenant_id, session_id=session_id,
            connection_key=connection.connection_key, space_id=space_id,
        )
        if conversation_id:
            started = await client.genie_create_message(
                space_id=space_id, conversation_id=conversation_id, content=question
            )
            message_id = str(started.get("message_id") or started.get("id") or "")
        else:
            started = await client.genie_start_conversation(
                space_id=space_id, content=question
            )
            conversation_id = str(started.get("conversation_id") or "")
            message = started.get("message") or {}
            message_id = str(
                started.get("message_id") or message.get("message_id")
                or message.get("id") or ""
            )
            if conversation_id:
                await self._remember_conversation(
                    tenant_id=tenant_id, session_id=session_id,
                    connection_key=connection.connection_key,
                    space_id=space_id, conversation_id=conversation_id,
                )
        if not conversation_id or not message_id:
            raise ExternalServiceError("Genie did not return a conversation/message id.")

        message = await self._poll_message(
            client, space_id=space_id,
            conversation_id=conversation_id, message_id=message_id,
        )
        return await self._collect_answer(
            client, message, space_id=space_id,
            conversation_id=conversation_id, message_id=message_id,
        )

    async def _poll_message(
        self, client: DatabricksRestClient, *, space_id: str,
        conversation_id: str, message_id: str,
    ) -> dict:
        waited = 0.0
        delay = self._settings.poll_initial_seconds
        while True:
            message = await client.genie_get_message(
                space_id=space_id, conversation_id=conversation_id, message_id=message_id
            )
            status = str(message.get("status") or "").upper()
            if status in _TERMINAL_OK:
                return message
            if status in _TERMINAL_BAD:
                error = str((message.get("error") or {}).get("error") or status)
                raise ExternalServiceError(f"Genie could not answer: {error}")
            if waited >= self._settings.max_wait_seconds:
                raise ExternalServiceError(
                    f"Genie did not answer within {int(self._settings.max_wait_seconds)}s "
                    "— check that the space's warehouse is serverless/running."
                )
            await asyncio.sleep(delay)
            waited += delay
            delay = min(delay * 2, self._settings.poll_max_seconds)

    async def _collect_answer(
        self, client: DatabricksRestClient, message: dict, *, space_id: str,
        conversation_id: str, message_id: str,
    ) -> DataAnswer:
        texts: list[str] = []
        sql = ""
        columns: tuple[str, ...] = ()
        rows: tuple[tuple, ...] = ()
        truncated = False
        warnings: list[str] = []

        for attachment in message.get("attachments") or []:
            text_part = (attachment.get("text") or {}).get("content")
            if text_part:
                texts.append(str(text_part))
            query = attachment.get("query") or {}
            if query:
                sql = str(query.get("query") or "")
                attachment_id = str(attachment.get("attachment_id") or "")
                if attachment_id:
                    try:
                        result = await client.genie_get_query_result(
                            space_id=space_id, conversation_id=conversation_id,
                            message_id=message_id, attachment_id=attachment_id,
                        )
                        columns, rows, truncated = self._parse_statement_response(
                            result.get("statement_response") or {}
                        )
                    except ExternalServiceError as exc:
                        warnings.append(f"query result unavailable: {exc.client_message()}")

        return DataAnswer(
            text="\n".join(texts).strip(), sql=sql, columns=columns, rows=rows,
            row_count=len(rows), truncated=truncated, warnings=tuple(warnings),
        )

    # ------------------------------------------------------------------ #
    # Direct SQL — the fast lane                                          #
    # ------------------------------------------------------------------ #

    async def execute_sql(
        self, *, connection: ConnectionEntity, statement: str
    ) -> DataAnswer:
        cleaned = (statement or "").strip()
        if not cleaned:
            raise ValidationError("The SQL statement must not be empty.")
        config = dict(connection.config or {})
        client = self._factory.get_client(str(config.get("workspace") or ""))
        response = await client.sql_execute(
            warehouse_id=str(config.get("warehouse_id") or ""),
            statement=cleaned,
            catalog=str(config.get("catalog") or "") or None,
            schema=str(config.get("schema") or "") or None,
            wait_timeout=self._settings.statement_wait_timeout,
            row_limit=self._settings.max_result_rows,
        )
        status = str((response.get("status") or {}).get("state") or "").upper()
        if status not in ("SUCCEEDED",):
            error = (response.get("status") or {}).get("error") or {}
            raise ExternalServiceError(
                f"SQL execution {status or 'failed'}: {error.get('message') or ''}"
            )
        columns, rows, truncated = self._parse_statement_response(response)
        return DataAnswer(
            sql=cleaned, columns=columns, rows=rows,
            row_count=len(rows), truncated=truncated,
        )

    def _parse_statement_response(
        self, statement_response: dict
    ) -> tuple[tuple[str, ...], tuple[tuple, ...], bool]:
        manifest = statement_response.get("manifest") or {}
        schema = manifest.get("schema") or {}
        columns = tuple(
            str(col.get("name") or "") for col in schema.get("columns") or []
        )
        result = statement_response.get("result") or {}
        data = result.get("data_array") or []
        cap = self._settings.max_result_rows
        truncated = bool(manifest.get("truncated")) or len(data) > cap
        rows = tuple(tuple(row) for row in data[:cap])
        return columns, rows, truncated

    # ------------------------------------------------------------------ #
    # Conversation memory (platform-owned; agents stay stateless)         #
    # ------------------------------------------------------------------ #

    async def _conversation_for(
        self, *, tenant_id: str, session_id: str, connection_key: str, space_id: str
    ) -> str:
        if not session_id:
            return ""
        try:
            async with self._db.session() as session:
                row = (
                    await session.exec(
                        select(GenieConversationEntity).where(
                            GenieConversationEntity.tenant_id == tenant_id,
                            GenieConversationEntity.session_id == session_id,
                            GenieConversationEntity.connection_key == connection_key,
                        )
                    )
                ).first()
                if row is None or row.space_id != space_id:
                    return ""
                return row.conversation_id
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def _remember_conversation(
        self, *, tenant_id: str, session_id: str, connection_key: str,
        space_id: str, conversation_id: str,
    ) -> None:
        if not session_id:
            return
        try:
            async with self._db.session() as session:
                session.add(
                    GenieConversationEntity(
                        id=uuid.uuid4().hex,
                        tenant_id=tenant_id,
                        session_id=session_id,
                        connection_key=connection_key,
                        space_id=space_id,
                        conversation_id=conversation_id,
                    )
                )
        except Exception as exc:  # noqa: BLE001 — memory is an optimization, never fatal
            logger.warning("Could not persist Genie conversation mapping", exc_info=True)


_service: DataQueryService | None = None


def get_data_query_service() -> DataQueryService:
    global _service
    if _service is None:
        _service = DataQueryService()
    return _service
