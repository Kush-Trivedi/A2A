"""General Assistant — the platform's navigation agent and router fallback.

Terminal (never delegates, never re-routes). Every answer is generated
fresh by the LLM from the LIVE Casbin-scoped catalog aubrey returns for
THIS user — a newly activated agent shows up in the very next answer, and
nothing is ever a canned string."""

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


def _catalog_lines(agents: list[dict]) -> str:
    if not agents:
        return "(none — the user's roles have no assistants assigned)"
    return "\n".join(
        f"- {a['display_name']} (team: {a['team_key']}): {a['description']}"
        for a in agents
    )


async def answer_stream(
    question: str, envelope: ContextEnvelope | None
) -> AsyncIterator[str]:
    if envelope is None:
        raise RuntimeError(
            "No context envelope received — this agent is called through "
            "aubrey's chat plane, which always sends one."
        )
    agents = await capabilities.accessible_agents(envelope=envelope)
    grounding = prompts.render(
        "catalog_header",
        roles=", ".join(envelope.roles) or "none",
        catalog=_catalog_lines(agents),
    )
    system = prompts.render("system", display_name=config.display_name)
    messages = [{"role": "system", "content": f"{system}\n\n{grounding}"}]
    messages.extend(dict(w) for w in envelope.window)
    messages.append({"role": "user", "content": question})

    async for token in capabilities.llm_chat_stream(
        envelope=envelope, messages=messages
    ):
        yield token


app = build_agent_app(config, answer_stream, capability_client=capabilities)

if __name__ == "__main__":
    uvicorn.run(app, host=config.host, port=config.port)
