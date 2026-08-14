"""Blood Pressure Outreach — an SMS campaign agent.

Same kit skeleton as every agent; the only difference is the audience.
Aubrey's SMS channel dispatches two kinds of turns here: outreach
("compose the SMS for this recipient" + context facts) and, for
bidirectional campaigns, member replies. The manifest prompt owns the
tone, the length discipline and the least-PHI rules — SMS is not a secure
channel, so nothing clinical ever goes in a message body."""

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


async def answer_stream(
    question: str, envelope: ContextEnvelope | None
) -> AsyncIterator[str]:
    if envelope is None:
        raise RuntimeError(
            "No context envelope received — this agent is called through "
            "aubrey's SMS channel, which always sends one."
        )
    system = prompts.render("system", display_name=config.display_name)
    messages = [{"role": "system", "content": system or ""}]
    messages.extend(dict(w) for w in envelope.window)
    messages.append({"role": "user", "content": question})

    async for token in capabilities.llm_chat_stream(
        envelope=envelope, messages=messages
    ):
        yield token


app = build_agent_app(config, answer_stream, capability_client=capabilities)

if __name__ == "__main__":
    uvicorn.run(app, host=config.host, port=config.port)
