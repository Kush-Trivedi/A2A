"""GDA Agent — live Databricks answers on the chat UI.

The team's data never leaves Databricks: questions go through ACE's genie
capability, which resolves the TEAM's registered databricks connection
(host + PAT held encrypted in ACE, genie space chosen by the team in their
env yaml). Results come back as text + SQL + rows for the canvas.
"""

from google.protobuf import json_format

from ace_agent_kit import AceCapabilityClient, ContextEnvelope

from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue

from .config import AgentConfig, get_agent_config

_MAX_ROWS = 20


class GdaAgent:
    def __init__(
        self,
        config: AgentConfig | None = None,
        capabilities: AceCapabilityClient | None = None,
    ) -> None:
        self._config = config or get_agent_config()
        self._capabilities = capabilities or AceCapabilityClient(
            base_url=self._config.ace_base_url
        )

    def _databricks(self) -> dict:
        return dict(self._config.connections.get("databricks") or {})

    async def handle(self, *, user_input: str, envelope: ContextEnvelope | None) -> str:
        question = user_input.strip() or "(empty question)"
        if envelope is None:
            return f"[{self._config.display_name}] Caller context required."

        databricks = self._databricks()
        connection = str(databricks.get("connection", "") or "")
        genie_space = str(databricks.get("genie_space", "") or "")
        if not databricks.get("enabled") or not connection or not genie_space:
            return (
                f"[{self._config.display_name}] My Databricks connection is not "
                "configured yet — the owning team needs to set "
                "connections.databricks.{enabled, connection, genie_space} in my "
                "env config and register the connection with ACE."
            )

        result = await self._capabilities.genie_query(
            envelope=envelope,
            agent_key=self._config.agent_key,
            connection=connection,
            genie_space=genie_space,
            question=question,
        )
        return self._format(question, result)

    def _format(self, question: str, result: dict) -> str:
        lines = [f"[{self._config.display_name}] {result.get('answer', '').strip()}".strip()]
        sql = str(result.get("sql", "") or "")
        if sql:
            lines.append(f"\nSQL used:\n```sql\n{sql}\n```")
        columns = list(result.get("columns") or [])
        rows = list(result.get("rows") or [])
        if columns and rows:
            lines.append("\n| " + " | ".join(str(c) for c in columns) + " |")
            lines.append("|" + "---|" * len(columns))
            for row in rows[:_MAX_ROWS]:
                lines.append("| " + " | ".join(str(v) for v in row) + " |")
            if len(rows) > _MAX_ROWS:
                lines.append(f"\n({len(rows) - _MAX_ROWS} more rows not shown)")
        return "\n".join(line for line in lines if line)


class TeamAgentExecutor(AgentExecutor):
    def __init__(self) -> None:
        self._agent = GdaAgent()

    @staticmethod
    def _envelope_from(context: RequestContext) -> ContextEnvelope | None:
        if context.message is None:
            return None
        metadata = json_format.MessageToDict(context.message.metadata)
        return ContextEnvelope.from_metadata(metadata)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        result = await self._agent.handle(
            user_input=context.get_user_input(),
            envelope=self._envelope_from(context),
        )
        await event_queue.enqueue_event(
            new_text_message(
                result, context_id=context.context_id, task_id=context.task_id
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancellation is not supported by this agent.")
