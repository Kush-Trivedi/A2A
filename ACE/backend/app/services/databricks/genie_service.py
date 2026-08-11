import asyncio
from dataclasses import dataclass, field
from typing import Any

from ...config.settings_validator import PlaceholderPolicy
from ...database.databricks.workspace_client_factory import (
    DatabricksWorkspaceClientFactory,
    get_workspace_client_factory,
)
from ...utils.common.logger import Logger
from ...utils.errors import ExternalServiceError, ValidationError

logger = Logger(__name__).get_logger()


@dataclass(frozen=True)
class GenieAnswer:
    text: str
    sql: str = ""
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    conversation_id: str = ""
    message_id: str = ""

    def to_artifact_data(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"answer": self.text}
        if self.sql:
            payload["sql"] = self.sql
        if self.columns:
            payload["columns"] = list(self.columns)
            payload["rows"] = [list(row) for row in self.rows]
        return payload


class GenieService:
    """Natural-language questions against a team's Genie space.

    The space id is ALWAYS team-owned (registry `team_config.databricks.
    genie_space_id`) — ACE supplies only the workspace connection (PAT from
    yaml). Blocking SDK calls run in a worker thread.
    """

    def __init__(
        self, factory: DatabricksWorkspaceClientFactory | None = None
    ) -> None:
        self._factory = factory or get_workspace_client_factory()

    async def ask(self, *, space_id: str, question: str) -> GenieAnswer:
        return await self._ask_validated(space_id=space_id, question=question, host="", token="")

    async def ask_with_connection(
        self, *, host: str, token: str, space_id: str, question: str
    ) -> GenieAnswer:
        """Team-connection variant: the workspace comes from the team's
        registered databricks connection, not from ACE yaml."""
        if not PlaceholderPolicy.is_configured(host) or not PlaceholderPolicy.is_configured(token):
            raise ValidationError(
                "The databricks connection is missing host/token. "
                "Check the team's connection registration."
            )
        return await self._ask_validated(
            space_id=space_id, question=question, host=host, token=token
        )

    async def _ask_validated(
        self, *, space_id: str, question: str, host: str, token: str
    ) -> GenieAnswer:
        normalized_space = (space_id or "").strip()
        if not PlaceholderPolicy.is_configured(normalized_space):
            raise ValidationError(
                "A Genie space id is required. Set the genie_space in the "
                "agent's databricks connection config.",
            )
        normalized_question = (question or "").strip()
        if not normalized_question:
            raise ValidationError("Question must not be empty.")

        try:
            return await asyncio.to_thread(
                self._ask_blocking, normalized_space, normalized_question, host, token
            )
        except (ValidationError, ExternalServiceError):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Genie query failed",
                extra={"space_id": normalized_space, "error_code": "genie_query_failed"},
                exc_info=True,
            )
            raise ExternalServiceError(
                "Genie query failed — check the databricks connection and genie space.",
                code="genie_query_failed",
                details={"space_id": normalized_space},
                cause=exc,
            ) from exc

    def _ask_blocking(
        self, space_id: str, question: str, host: str = "", token: str = ""
    ) -> GenieAnswer:
        if host and token:
            from databricks.sdk import WorkspaceClient

            genie = WorkspaceClient(host=host, token=token).genie
        else:
            genie = self._factory.get_client().genie
        message = genie.start_conversation_and_wait(space_id, question)

        text_parts: list[str] = []
        sql = ""
        columns: tuple[str, ...] = ()
        rows: tuple[tuple[Any, ...], ...] = ()

        for attachment in message.attachments or []:
            if attachment.text is not None and attachment.text.content:
                text_parts.append(attachment.text.content)
            if attachment.query is not None:
                sql = attachment.query.query or sql
                if attachment.query.description:
                    text_parts.append(attachment.query.description)
                if attachment.attachment_id:
                    columns, rows = self._fetch_result(
                        genie, space_id, message, attachment.attachment_id
                    )

        return GenieAnswer(
            text="\n".join(text_parts).strip(),
            sql=sql,
            columns=columns,
            rows=rows,
            conversation_id=message.conversation_id or "",
            message_id=message.message_id or message.id or "",
        )

    @staticmethod
    def _fetch_result(
        genie, space_id: str, message, attachment_id: str
    ) -> tuple[tuple[str, ...], tuple[tuple[Any, ...], ...]]:
        try:
            result = genie.get_message_query_result_by_attachment(
                space_id,
                message.conversation_id,
                message.message_id or message.id,
                attachment_id,
            )
            statement = result.statement_response
            if statement is None or statement.result is None:
                return (), ()
            manifest = statement.manifest
            columns = tuple(
                column.name or ""
                for column in (manifest.schema.columns if manifest and manifest.schema else [])
            )
            rows = tuple(
                tuple(row) for row in (statement.result.data_array or [])
            )
            return columns, rows
        except Exception:  # noqa: BLE001 — result fetch is best-effort
            logger.warning(
                "Genie query result fetch failed; returning text/SQL only",
                extra={"space_id": space_id},
                exc_info=True,
            )
            return (), ()


_service: GenieService | None = None


def get_genie_service() -> GenieService:
    global _service
    if _service is None:
        _service = GenieService()
    return _service
