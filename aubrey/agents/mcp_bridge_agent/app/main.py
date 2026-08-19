"""Vendor Bridge — makes a purchased MCP capability a routable Aubrey agent.

Buy-now lane of the vendor gateway: register the vendor's MCP server as a
team connection, copy this template, put the vendor's real skills in the
manifest, and the vendor shows up in chat routing, the catalog, and the
dashboards like any team agent. The platform holds the credentials,
audits every tool call, and the LLM here does only two small jobs: pick
the right vendor tool for the question, then turn the tool's result into
a clear answer. Swap the vendor for an internal build later and nothing
upstream changes."""

import json
import time
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

_mcp = dict(config.settings.get("mcp") or {})
_connection = str(_mcp.get("connection") or "")
_cache_seconds = float(_mcp.get("tools_cache_seconds") or 300)
_tools_cache: dict = {"at": 0.0, "tools": []}


async def _tools(envelope: ContextEnvelope) -> list[dict]:
    if time.monotonic() - _tools_cache["at"] > _cache_seconds:
        _tools_cache["tools"] = await capabilities.mcp_tools(
            envelope=envelope, connection_key=_connection
        )
        _tools_cache["at"] = time.monotonic()
    return _tools_cache["tools"]


async def _llm_once(envelope: ContextEnvelope, messages: list[dict]) -> str:
    chunks = []
    async for token in capabilities.llm_chat_stream(envelope=envelope, messages=messages):
        chunks.append(token)
    return "".join(chunks).strip()


def _parse_selection(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned[4:] if cleaned.lower().startswith("json") else cleaned
    try:
        return dict(json.loads(cleaned))
    except Exception:  # noqa: BLE001 — treat unparseable output as a direct answer
        return {"answer": raw}


async def answer_stream(
    question: str, envelope: ContextEnvelope | None
) -> AsyncIterator[str]:
    if envelope is None:
        raise RuntimeError(
            "No context envelope received — this agent is called through "
            "aubrey's chat plane, which always sends one."
        )
    tools = await _tools(envelope)
    tool_lines = "\n".join(
        f"- {t['name']}: {t.get('description') or ''} | schema: "
        f"{json.dumps(t.get('input_schema') or {})}"
        for t in tools
    ) or "(no tools available)"

    selection_raw = await _llm_once(
        envelope,
        [
            {"role": "system", "content": prompts.render("select_tool", tools=tool_lines) or ""},
            {"role": "user", "content": question},
        ],
    )
    selection = _parse_selection(selection_raw)

    if "answer" in selection:
        text = str(selection.get("answer") or "")
        if text.strip() == "NOT_SUPPORTED":
            yield prompts.render("not_supported", display_name=config.display_name) or ""
        else:
            yield text
        return

    tool = str(selection.get("tool") or "")
    arguments = dict(selection.get("arguments") or {})
    result = await capabilities.mcp_call(
        envelope=envelope, connection_key=_connection, tool=tool, arguments=arguments
    )
    if result.get("is_error"):
        yield prompts.render("vendor_error", question=question) or ""
        return

    result_text = str(result.get("text") or "") or json.dumps(
        result.get("structured") or {}
    )
    system = prompts.render("system", display_name=config.display_name)
    glue = prompts.render("tool_context", tool=tool, result=result_text)
    messages = [{"role": "system", "content": f"{system}\n\n{glue}"}]
    messages.extend(dict(w) for w in envelope.window)
    messages.append({"role": "user", "content": question})

    async for token in capabilities.llm_chat_stream(envelope=envelope, messages=messages):
        yield token


app = build_agent_app(config, answer_stream, capability_client=capabilities)

if __name__ == "__main__":
    uvicorn.run(app, host=config.host, port=config.port)
