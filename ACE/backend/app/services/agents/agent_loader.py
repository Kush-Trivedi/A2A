from pathlib import Path
import yaml
from ...config.application_context import get_application_context
from ...prompts import PromptRepository, get_prompt_repository
from ...utils.common.logger import Logger
from .agent_definition import AgentDefinition

logger = Logger(__name__).get_logger()


def _as_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return ()


def _tool_names(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        names: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                names.append(item.strip())
            elif isinstance(item, dict) and str(item.get("name", "")).strip():
                names.append(str(item["name"]).strip())
        return tuple(names)
    return _as_tuple(value)


def _definition_from_yaml(data: dict, *, source: str) -> AgentDefinition:
    agent_id = str(data.get("id") or source).strip().lower()
    if not agent_id:
        raise ValueError(f"Agent package '{source}' is missing an 'id'.")
    return AgentDefinition(
        id=agent_id,
        display_name=str(data.get("display_name") or agent_id),
        description=str(data.get("description") or ""),
        prompt_name=(str(data["prompt_name"]) if data.get("prompt_name") else None),
        knowledge_sources=_as_tuple(data.get("knowledge_sources")),
        aliases=_as_tuple(data.get("aliases")),
        tools=_tool_names(data.get("tools")),
        model=(str(data["model"]) if data.get("model") else None),
        include_session_uploads=bool(data.get("include_session_uploads", True)),
        strict_grounding=bool(data.get("strict_grounding", False)),
        permission=(str(data["permission"]) if data.get("permission") else None),
        retrieval_mode=(str(data["retrieval_mode"]) if data.get("retrieval_mode") else None),
    )


def _load_agent_package(
    path: Path, repo: PromptRepository
) -> AgentDefinition | None:
    manifest = path / "agent.yaml"
    if not manifest.is_file():
        return None
    try:
        prompt_dir = path / "prompts"
        if prompt_dir.is_dir():
            repo.add_path(prompt_dir)

        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("agent.yaml must be a mapping.")
        definition = _definition_from_yaml(data, source=path.name)
        logger.info(
            "Agent package discovered",
            extra={"agent_id": definition.id, "package": path.name},
        )
        return definition
    except Exception as exc:
        logger.error(
            "Failed to load agent package; skipping",
            extra={"package": path.name, "error": str(exc)},
            exc_info=True,
        )
        return None


def discover_external_agents(
    *,
    paths: list[str] | None = None,
    prompts: PromptRepository | None = None,
) -> list[AgentDefinition]:
    raw = paths
    if raw is None:
        configured = get_application_context().agents.get("external_paths") or []
        if isinstance(configured, str):
            configured = configured.split(",")
        raw = [str(p) for p in configured]
        if not raw:
            return []

    repo = prompts or get_prompt_repository()
    discovered: list[AgentDefinition] = []
    seen: set[Path] = set()
    for entry in raw:
        entry = (entry or "").strip()
        if not entry:
            continue
        root = Path(entry).expanduser()
        if not root.is_dir():
            logger.warning(
                "External agent path not found; skipping",
                extra={"path": str(root)},
            )
            continue
        root = root.resolve()
        if root in seen:
            continue
        seen.add(root)
        if (root / "agent.yaml").is_file():
            candidates = [root]
        else:
            candidates = sorted(
                p for p in root.iterdir()
                if p.is_dir() and not p.name.startswith((".", "_"))
            )
        for path in candidates:
            definition = _load_agent_package(path=path, repo=repo)
            if definition is not None:
                discovered.append(definition)
    return discovered
