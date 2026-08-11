from ...config.application_context import get_application_context
from ...utils.common.logger import Logger
from .agent_definition import AgentDefinition
from .agent_loader import discover_external_agents

logger = Logger(__name__).get_logger()


def _csv(value: object) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return tuple(item.strip() for item in str(value).split(",") if item.strip())


class AgentRegistry:
    def __init__(self, *, default_id: str = "default") -> None:
        self._default_id = default_id
        self._by_id: dict[str, AgentDefinition] = {}
        self._aliases: dict[str, str] = {}

    def register(self, definition: AgentDefinition) -> None:
        key = definition.id.strip().lower()
        self._by_id[key] = definition
        for alias in definition.aliases:
            self._aliases[alias.strip().lower()] = key

    def register_agents(self, definitions: list[AgentDefinition]) -> None:
        for definition in definitions:
            key = definition.id.strip().lower()
            if key in self._by_id:
                logger.warning(
                    "Agent id collides with an existing agent — skipping",
                    extra={"agent_id": key},
                )
                continue
            self.register(definition)

    def resolve(self, agent_id: str | None) -> AgentDefinition:
        normalized = (agent_id or "").strip().lower()
        if not normalized:
            return self.default
        if normalized in self._by_id:
            return self._by_id[normalized]
        if normalized in self._aliases:
            return self._by_id[self._aliases[normalized]]
        logger.info(
            "Unknown agent id — falling back to default",
            extra={"requested_agent": agent_id, "default": self._default_id},
        )
        return self.default

    @property
    def default(self) -> AgentDefinition:
        return self._by_id[self._default_id]

    def list(self) -> list[AgentDefinition]:
        return list(self._by_id.values())


def _build_default_registry() -> AgentRegistry:
    registry = AgentRegistry(default_id="default")
    registry.register(
        AgentDefinition(
            id="default",
            display_name="Ace Assistant",
            description="General-purpose assistant with access to your uploads.",
            aliases=("ace", "assistant", "orchestrator", "chat"),
            include_session_uploads=True,
        )
    )

    knowledge_sources = _csv(get_application_context().agents.get("knowledge_agent_sources"))
    registry.register(
        AgentDefinition(
            id="knowledge",
            display_name="Knowledge Assistant",
            description="Answers grounded in configured knowledge sources.",
            aliases=("kb", "knowledge_base", "docs"),
            prompt_name="agent.knowledge.system",
            knowledge_sources=knowledge_sources,
            include_session_uploads=True,
            strict_grounding=True,
        )
    )

    registry.register(
        AgentDefinition(
            id="file_upload",
            display_name="File Q&A",
            description="Answers questions about a file you upload — nothing else.",
            aliases=("upload", "file", "document", "file_qa"),
            prompt_name="agent.file_upload.system",
            knowledge_sources=(),
            include_session_uploads=True,
            strict_grounding=True,
        )
    )

    registry.register_agents(discover_external_agents())
    return registry


_registry: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    global _registry
    if _registry is None:
        _registry = _build_default_registry()
    return _registry
