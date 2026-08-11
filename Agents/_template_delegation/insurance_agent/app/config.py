from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ace_agent_kit import DelegationTarget

from .auth import AgentAuthSettings

_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "agent.yaml"


@dataclass(frozen=True)
class SkillConfig:
    id: str
    name: str
    description: str
    tags: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentConfig:
    team_key: str
    agent_key: str
    display_name: str
    description: str
    version: str
    host: str
    port: int
    skills: tuple[SkillConfig, ...]
    data: dict[str, Any] = field(default_factory=dict)
    ace_base_url: str = "http://localhost:3000"
    public_url: str = "http://localhost:3100"
    permission: str = "chat"
    allowed_roles: tuple[str, ...] = ()
    retrieval_mode: str | None = None
    knowledge_sources: tuple[str, ...] = ()
    auth: AgentAuthSettings = field(
        default_factory=lambda: AgentAuthSettings(
            enabled=False, tenant_id="", audience=""
        )
    )
    delegations: dict[str, DelegationTarget] = field(default_factory=dict)


@lru_cache(maxsize=1)
def get_agent_config() -> AgentConfig:
    with _MANIFEST_PATH.open("r", encoding="utf-8-sig") as file:
        manifest = yaml.safe_load(file) or {}

    agent = manifest.get("agent", {}) or {}
    server = manifest.get("server", {}) or {}
    ace = manifest.get("ace", {}) or {}
    auth = manifest.get("auth", {}) or {}

    delegations = {
        str(capability): DelegationTarget(
            capability=str(capability),
            card_url=str(target.get("card_url", "") or ""),
            audience=str(target.get("audience", "") or ""),
        )
        for capability, target in (manifest.get("delegations", {}) or {}).items()
        if isinstance(target, dict) and target.get("card_url")
    }

    skills = tuple(
        SkillConfig(
            id=str(skill["id"]),
            name=str(skill["name"]),
            description=str(skill.get("description", "")),
            tags=tuple(str(t) for t in skill.get("tags", [])),
            examples=tuple(str(e) for e in skill.get("examples", [])),
        )
        for skill in manifest.get("skills", [])
    )
    if not skills:
        raise ValueError("agent.yaml must declare at least one skill.")

    return AgentConfig(
        team_key=str(agent["team_key"]),
        agent_key=str(agent["agent_key"]),
        display_name=str(agent["display_name"]),
        description=str(agent.get("description", "")),
        version=str(agent.get("version", "0.1.0")),
        host=str(server.get("host", "0.0.0.0")),
        port=int(server.get("port", 3100)),
        skills=skills,
        data=dict(manifest.get("data", {}) or {}),
        ace_base_url=str(ace.get("base_url", "http://localhost:3000")).rstrip("/"),
        public_url=str(ace.get("public_url", "http://localhost:3100")).rstrip("/"),
        permission=str(ace.get("permission", "chat")),
        allowed_roles=tuple(str(r) for r in ace.get("allowed_roles", [])),
        retrieval_mode=(str(ace["retrieval_mode"]) if ace.get("retrieval_mode") else None),
        knowledge_sources=tuple(str(s) for s in ace.get("knowledge_sources", [])),
        delegations=delegations,
        auth=AgentAuthSettings(
            enabled=bool(auth.get("enabled", False)),
            tenant_id=str(auth.get("tenant_id", "") or ""),
            audience=str(auth.get("audience", "") or ""),
            authority=str(
                auth.get("authority") or "https://login.microsoftonline.com"
            ).rstrip("/"),
            issuer_override=str(auth.get("issuer", "") or ""),
            jwks_url_override=str(auth.get("jwks_url", "") or ""),
        ),
    )
