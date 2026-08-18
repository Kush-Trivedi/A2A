"""The fast data lane — our own text-to-SQL instead of Genie.

One LLM call writes the SQL from an auto-introspected schema plus
team-owned few-shot examples; the Statement Execution API runs it. ~5-8s
end to end vs Genie's 15-30s reasoning loop, paid through the LLM spend
we already meter per team.

The contract is graceful in every branch: answerable → SQL → rows;
not answerable from this data → {answerable: false, reason} so the agent
replies conversationally; a failing statement gets ONE repair attempt
(error fed back) and then an honest failure — never silence.

Guards: generated SQL must be a single SELECT/WITH statement (DML/DDL
keywords rejected), row caps apply, and the workspace PAT should be
read-only — a bad generation can waste seconds, never mutate data."""

import re
import time
from dataclasses import dataclass
from functools import lru_cache

from ...config.application_context import get_application_context
from ...entity.documents import ConnectionEntity
from ...llm.azure_foundry import get_ace_azure_foundry
from ...utils.common.logger import Logger
from ...utils.errors import ExternalServiceError
from .data_query_service import DataAnswer, get_data_query_service

logger = Logger(__name__).get_logger()

_NO_QUERY_MARKER = "NO_QUERY"
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|merge|copy)\b",
    re.IGNORECASE,
)

_DEFAULT_PROMPT = (
    "You translate questions into a single Databricks SQL SELECT statement "
    "over the tables below. Use ONLY these tables and columns.\n\n"
    "Schema:\n{schema}\n\nExamples:\n{examples}\n\n"
    "Rules: return ONLY the SQL statement, no explanation, no markdown. "
    "If the question cannot be answered from these tables, return exactly: "
    "NO_QUERY: <one short reason>."
)


@dataclass(frozen=True)
class Text2SqlSettings:
    prompt_template: str
    schema_cache_ttl_seconds: float
    repair_attempts: int
    max_output_tokens: int


@lru_cache(maxsize=1)
def get_text2sql_settings() -> Text2SqlSettings:
    cfg = (get_application_context().databricks.get("data") or {}).get("text2sql") or {}
    return Text2SqlSettings(
        prompt_template=str(cfg.get("prompt_template") or _DEFAULT_PROMPT),
        schema_cache_ttl_seconds=float(cfg.get("schema_cache_ttl_seconds") or 600),
        repair_attempts=int(cfg.get("repair_attempts") or 1),
        max_output_tokens=int(cfg.get("max_output_tokens") or 800),
    )


class Text2SqlService:
    def __init__(self) -> None:
        self._settings = get_text2sql_settings()
        self._data = get_data_query_service()
        # (connection_id) -> (schema_text, fetched_at)
        self._schema_cache: dict[str, tuple[str, float]] = {}

    async def ask(
        self,
        *,
        connection: ConnectionEntity,
        question: str,
        examples: list[dict] | None = None,
    ) -> DataAnswer:
        schema = await self._schema_for(connection)
        prompt = self._settings.prompt_template.replace("{schema}", schema).replace(
            "{examples}", self._examples_block(examples)
        )
        sql = await self._generate(prompt, question)
        if sql.upper().startswith(_NO_QUERY_MARKER):
            reason = sql.partition(":")[2].strip() or "not answerable from this data"
            return DataAnswer(answerable=False, reason=reason)

        self._guard(sql)
        try:
            return await self._data.execute_sql(connection=connection, statement=sql)
        except ExternalServiceError as exc:
            if self._settings.repair_attempts < 1:
                raise
            logger.warning("Generated SQL failed — one repair attempt", exc_info=True)
            repaired = await self._generate(
                prompt,
                f"{question}\n\nYour previous SQL failed with: "
                f"{exc.client_message()}\nPrevious SQL: {sql}\nReturn corrected SQL.",
            )
            if repaired.upper().startswith(_NO_QUERY_MARKER):
                reason = repaired.partition(":")[2].strip() or "could not compute"
                return DataAnswer(answerable=False, reason=reason)
            self._guard(repaired)
            return await self._data.execute_sql(connection=connection, statement=repaired)

    async def _generate(self, system_prompt: str, user_content: str) -> str:
        raw = await get_ace_azure_foundry().acomplete_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_output_tokens=self._settings.max_output_tokens,
        )
        text = str(raw or "").strip()
        # strip markdown fences if the model added them anyway
        text = re.sub(r"^```(?:sql)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
        return text

    def _guard(self, sql: str) -> None:
        cleaned = re.sub(r"--.*?$|/\*.*?\*/", " ", sql, flags=re.MULTILINE | re.DOTALL)
        statements = [s for s in cleaned.split(";") if s.strip()]
        first = statements[0].strip().lower() if statements else ""
        if len(statements) != 1 or not (
            first.startswith("select") or first.startswith("with")
        ):
            raise ExternalServiceError("Generated SQL was not a single SELECT statement.")
        if _FORBIDDEN.search(cleaned):
            raise ExternalServiceError("Generated SQL contained a forbidden keyword.")

    @staticmethod
    def _examples_block(examples: list[dict] | None) -> str:
        if not examples:
            return "(none)"
        lines = []
        for ex in examples:
            q = str(ex.get("question") or "").strip()
            s = str(ex.get("sql") or "").strip()
            if q and s:
                lines.append(f"Q: {q}\nSQL: {s}")
        return "\n\n".join(lines) or "(none)"

    async def _schema_for(self, connection: ConnectionEntity) -> str:
        cached = self._schema_cache.get(connection.id)
        if cached and (time.monotonic() - cached[1]) < self._settings.schema_cache_ttl_seconds:
            return cached[0]
        config = dict(connection.config or {})
        catalog = str(config.get("catalog") or "")
        schema = str(config.get("schema") or "")
        tables_filter = ""
        allowlist = [
            t.strip() for t in str(config.get("tables") or "").split(",") if t.strip()
        ]
        if allowlist:
            quoted = ", ".join(f"'{t}'" for t in allowlist)
            tables_filter = f"AND table_name IN ({quoted})"
        statement = (
            "SELECT table_name, column_name, data_type, "
            "COALESCE(comment, '') AS comment "
            f"FROM {catalog}.information_schema.columns "
            f"WHERE table_schema = '{schema}' {tables_filter} "
            "ORDER BY table_name, ordinal_position"
        )
        result = await self._data.execute_sql(connection=connection, statement=statement)
        lines: list[str] = []
        current = ""
        for row in result.rows:
            table, column, dtype, comment = (list(row) + ["", "", "", ""])[:4]
            if table != current:
                current = str(table)
                lines.append(f"\nTable {catalog}.{schema}.{current}:")
            note = f" -- {comment}" if comment else ""
            lines.append(f"  {column} {dtype}{note}")
        text = "\n".join(lines).strip() or "(no tables found)"
        self._schema_cache[connection.id] = (text, time.monotonic())
        return text


_service: Text2SqlService | None = None


def get_text2sql_service() -> Text2SqlService:
    global _service
    if _service is None:
        _service = Text2SqlService()
    return _service
