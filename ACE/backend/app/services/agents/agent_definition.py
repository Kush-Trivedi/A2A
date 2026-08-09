from dataclasses import dataclass

DEFAULT_SYSTEM_PROMPT = (
    "You are an AI agent designed to assist users with a variety of tasks. "
)

@dataclass
class AgentDefinition:
    id: str
    display_name: str
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    prompt_name: str | None = None
    knowledge_sources: tuple[str, ...] = ()
    model: str | None = None
    description: str = ""
    aliases: tuple[str, ...] = ()
    include_session_uploads: bool = True
    strict_grounding: bool = False
    tools: tuple[str, ...] = ()
    permission: str | None = None
    retrieval_mode: str | None = None

    def uses_knowledge(self) -> bool:
        return self.include_session_uploads or bool(self.knowledge_sources)
