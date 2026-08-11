"""Agent configuration — built on the kit's AgentContext.

Manifest (agent.yaml, env-invariant) + config/env/<ENV>.yaml (env values,
Key Vault lookups resolved) merge into one frozen AgentConfig. Nothing in
this file changes between environments — set ENV and go.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Mapping

from ace_agent_kit import (
    AgentContext,
    AgentSettingsValidator,
    PromptStore,
    get_agent_context,
)

from .auth import AgentAuthSettings


@dataclass(frozen=True)
class SkillDefinition:
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
    ace_base_url: str
    public_url: str
    permission: str
    allowed_roles: tuple[str, ...]
    registration_token: str
    auth: AgentAuthSettings
    llm_default: str
    llm_deployments: Mapping[str, str]
    retrieval_mode: str
    knowledge_sources: tuple[str, ...]
    connections: Mapping[str, Any]
    channels: Mapping[str, Any]
    skills: tuple[SkillDefinition, ...]
    prompt_store: PromptStore
    delegations: Mapping[str, Any] = field(default_factory=dict)

    @property
    def default_deployment(self) -> str:
        return str(self.llm_deployments.get(self.llm_default, "") or "")


class AgentConfigFactory:
    """Builds the frozen AgentConfig from AgentContext (manifest + env yaml)."""

    def __init__(self, context: AgentContext | None = None) -> None:
        self._context = context or get_agent_context()

    def build(self) -> AgentConfig:
        AgentSettingsValidator(self._context).validate_and_log()

        manifest = self._context.manifest
        agent = manifest.get("agent") or {}
        ace = self._context.ace
        server = self._context.server
        auth = self._context.auth
        llm = self._context.llm
        retrieval = self._context.retrieval

        return AgentConfig(
            team_key=str(agent.get("team_key", "")),
            agent_key=str(agent.get("agent_key", "")),
            display_name=str(agent.get("display_name", "")),
            description=str(agent.get("description", "")),
            version=str(agent.get("version", "0.1.0")),
            host=str(server.get("host", "0.0.0.0")),
            port=int(server.get("port", 0)),
            ace_base_url=str(ace.get("base_url", "")).rstrip("/"),
            public_url=str(ace.get("public_url", "")).rstrip("/"),
            permission=str(ace.get("permission", "chat")),
            allowed_roles=tuple(ace.get("allowed_roles") or []),
            registration_token=str(ace.get("registration_token", "")),
            auth=AgentAuthSettings(
                enabled=bool(auth.get("enabled", False)),
                tenant_id=str(auth.get("tenant_id", "")),
                audience=str(auth.get("audience", "")),
                authority=str(auth.get("authority", "https://login.microsoftonline.com")),
                issuer_override=str(auth.get("issuer", "")),
                jwks_url_override=str(auth.get("jwks_url", "")),
            ),
            llm_default=str(llm.get("default", "")),
            llm_deployments=dict(llm.get("deployments") or {}),
            retrieval_mode=str(retrieval.get("mode", "sparse")),
            knowledge_sources=tuple(retrieval.get("knowledge_sources") or []),
            connections=dict(self._context.connections),
            channels=dict(self._context.channels),
            skills=self._skills(manifest),
            prompt_store=PromptStore.from_manifest(manifest.get("prompts") or {}),
            delegations=dict(manifest.get("delegations") or {}),
        )

    @staticmethod
    def _skills(manifest: Mapping[str, Any]) -> tuple[SkillDefinition, ...]:
        skills: list[SkillDefinition] = []
        for raw in manifest.get("skills") or []:
            if not isinstance(raw, Mapping):
                continue
            skills.append(
                SkillDefinition(
                    id=str(raw.get("id", "")),
                    name=str(raw.get("name", "")),
                    description=str(raw.get("description", "")),
                    tags=tuple(str(t) for t in (raw.get("tags") or [])),
                    examples=tuple(str(e) for e in (raw.get("examples") or [])),
                )
            )
        return tuple(skills)


@lru_cache(maxsize=1)
def get_agent_config() -> AgentConfig:
    return AgentConfigFactory().build()
