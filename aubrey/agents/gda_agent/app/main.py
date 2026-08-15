"""GDA Assistant — live Databricks answers + grounded draft appeals.

Holds no data credentials: every question goes through aubrey's
/capability/data/genie with the team token, scoped to this team's
registered connection. Genie conversation continuity is platform-managed
per chat session, so follow-ups work while this agent stays stateless.
The manifest owns every prompt — answers and draft-appeal structure alike;
whether a turn is a question or a draft request is decided by the user's
words (or the UI pinning this agent with the row context), never by code."""

from collections.abc import AsyncIterator
from pathlib import Path

import uvicorn

from kit import (
    AubreyCapabilityClient,
    ContextEnvelope,
    PromptStore,
    build_agent_app,
    load_agent_config,
)

config = load_agent_config(Path(__file__).resolve().parent.parent / "agent.yaml")
prompts = PromptStore(config.prompts)
capabilities = AubreyCapabilityClient(
    base_url=config.aubrey_base_url,
    team_token=config.team_token,
    agent_key=config.agent_key,
)

_data = dict(config.settings.get("data") or {})
_genie_connection = str(_data.get("genie_connection") or "")
_max_table_rows = int(config.settings.get("max_table_rows") or 20)


def _table_block(columns: list, rows: list) -> str:
    if not rows:
        return "(no rows)"
    header = " | ".join(str(c) for c in columns)
    lines = [header, " | ".join("---" for _ in columns)]
    for row in rows[:_max_table_rows]:
        lines.append(" | ".join("" if v is None else str(v) for v in row))
    return "\n".join(lines)


async def answer_stream(
    question: str, envelope: ContextEnvelope | None
) -> AsyncIterator[str]:
    if envelope is None:
        raise RuntimeError(
            "No context envelope received — this agent is called through "
            "aubrey's chat plane, which always sends one."
        )
    result = await capabilities.data_genie(
        envelope=envelope, connection_key=_genie_connection, question=question
    )
    rows = list(result.get("rows") or [])
    genie_text = str(result.get("text") or "")
    if not rows and not genie_text:
        yield prompts.render("no_data", question=question) or ""
        return

    truncated_note = ", truncated" if result.get("truncated") else ""
    context = prompts.render(
        "data_context",
        sql=str(result.get("sql") or "(none)"),
        row_count=str(result.get("row_count") or len(rows)),
        truncated_note=truncated_note,
        table=_table_block(list(result.get("columns") or []), rows),
    )
    system = prompts.render("system", display_name=config.display_name)
    draft = prompts.render("draft_appeal")
    system_content = f"{system}\n\n{draft}\n\n{context}"
    if genie_text:
        system_content += f"\n\nGenie's own summary: {genie_text}"

    messages = [{"role": "system", "content": system_content}]
    messages.extend(dict(w) for w in envelope.window)
    messages.append({"role": "user", "content": question})

    async for token in capabilities.llm_chat_stream(
        envelope=envelope, messages=messages
    ):
        yield token


app = build_agent_app(config, answer_stream, capability_client=capabilities)

if __name__ == "__main__":
    uvicorn.run(app, host=config.host, port=config.port)
