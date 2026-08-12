"""Benefits Assistant — the retrieval agent.

Holds no database and no vector store: every answer starts with a
grant-scoped retrieve through aubrey (hybrid RRF + 1-hop graph over the
team's ingested documents), then streams the LLM answer grounded on those
chunks with their sources. No results is an honest, manifest-owned answer;
an LLM failure surfaces as a typed error — never a silent fallback."""

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

_retrieval = dict(config.settings.get("retrieval") or {})
_max_chunks = int(config.settings.get("max_context_chunks") or 6)


def _context_block(chunks: list[dict]) -> str:
    lines = []
    for chunk in chunks[:_max_chunks]:
        source = chunk.get("file_name") or chunk.get("source_uri") or "unknown"
        lines.append(f"[source: {source}]\n{chunk.get('content', '')}")
    return "\n\n".join(lines)


async def answer_stream(
    question: str, envelope: ContextEnvelope | None
) -> AsyncIterator[str]:
    if envelope is None:
        raise RuntimeError(
            "No context envelope received — this agent is called through "
            "aubrey's chat plane, which always sends one."
        )
    chunks = await capabilities.retrieve(
        envelope=envelope,
        query=question,
        mode=_retrieval.get("mode"),
        top_k=_retrieval.get("top_k"),
        min_similarity=_retrieval.get("min_similarity"),
    )
    if not chunks:
        yield prompts.render("no_results", question=question) or ""
        return

    system = prompts.render("system", display_name=config.display_name)
    glue = prompts.render("context_glue", context=_context_block(chunks))
    messages = [{"role": "system", "content": f"{system}\n\n{glue}"}]
    messages.extend(dict(w) for w in envelope.window)
    messages.append({"role": "user", "content": question})

    async for token in capabilities.llm_chat_stream(
        envelope=envelope, messages=messages
    ):
        yield token


app = build_agent_app(config, answer_stream, capability_client=capabilities)

if __name__ == "__main__":
    uvicorn.run(app, host=config.host, port=config.port)
