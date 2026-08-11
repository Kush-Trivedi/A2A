from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDefinition:
    """Value object describing a registered A2A agent for one chat turn.

    Built from the registry row in ConversationService._resolve_agent —
    ACE never generates answers itself, so this carries routing/authorization
    facts only (no prompts, no model settings: those are the agent's own).
    """

    id: str
    display_name: str
    description: str = ""
    aliases: tuple[str, ...] = ()
    knowledge_sources: tuple[str, ...] = ()
    permission: str | None = None
    retrieval_mode: str | None = None
