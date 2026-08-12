"""File Assistant — answers strictly within the user's uploaded documents.

Holds no database and no vector store: the uploaded files live against the
chat session in aubrey, fetched per turn through the session-scoped files
capability (tenant + user + session all enforced server-side). The prompt
hard-scopes answers to that content; no upload is an honest, manifest-owned
answer; an LLM failure surfaces as a typed error — never a silent
fallback."""

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

_max_context_chars = int(config.settings.get("max_context_chars") or 24000)


def _context_block(documents: list[dict]) -> str:
    """Newest uploads win the budget; a document that crosses it is cut
    with an explicit truncation marker so the model never assumes it saw
    everything."""
    blocks: list[str] = []
    remaining = _max_context_chars
    for document in reversed(documents):
        if remaining <= 0:
            break
        name = document.get("file_name") or "unknown"
        content = str(document.get("content") or "")
        if len(content) > remaining:
            content = (
                f"{content[:remaining]}\n[... {name} truncated — the full "
                "document exceeds the context budget]"
            )
        blocks.append(f"[file: {name}]\n{content}")
        remaining -= len(content)
    blocks.reverse()
    return "\n\n".join(blocks)


async def answer_stream(
    question: str, envelope: ContextEnvelope | None
) -> AsyncIterator[str]:
    if envelope is None:
        raise RuntimeError(
            "No context envelope received — this agent is called through "
            "aubrey's chat plane, which always sends one."
        )
    documents = await capabilities.session_documents(envelope=envelope)
    if not documents:
        yield prompts.render("no_file", question=question) or ""
        return

    system = prompts.render("system", display_name=config.display_name)
    glue = prompts.render("context_glue", context=_context_block(documents))
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
