"""Team-owned agent configuration — ONE manifest (agent.yaml) drives the
whole agent: identity, skills (= routing surface), prompts, and endpoints.
Zero prompt text and zero identity strings live in Python.

Operational values can be overridden by environment (same philosophy as
aubrey's env yamls); the team service token is env-ONLY, never a file:

    AGENT_TEAM_TOKEN   (required — the team's aubrey service token)
    AUBREY_BASE_URL    overrides aubrey.base_url
    AGENT_HOST / AGENT_PORT / AGENT_PUBLIC_URL   override server.*
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SkillConfig:
    id: str
    name: str
    description: str
    tags: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentConfig:
    agent_key: str
    team_key: str
    display_name: str
    description: str
    version: str
    permission: str
    allowed_roles: tuple[str, ...]
    skills: tuple[SkillConfig, ...]
    prompts: dict[str, Any]
    host: str
    port: int
    public_url: str
    aubrey_base_url: str
    team_token: str
    # agent-specific knobs (retrieval mode, output caps, ...) — team-owned
    settings: dict[str, Any] = field(default_factory=dict)

    @property
    def card_url(self) -> str:
        return f"{self.public_url.rstrip('/')}/.well-known/agent-card.json"


def load_agent_config(manifest_path: Path) -> AgentConfig:
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Agent manifest not found: {manifest_path}")
    with manifest_path.open("r", encoding="utf-8-sig") as fh:
        manifest = yaml.safe_load(fh) or {}

    agent = manifest["agent"]
    server = manifest["server"]
    aubrey = manifest["aubrey"]

    team_token = os.getenv("AGENT_TEAM_TOKEN", "").strip()
    if not team_token:
        raise ValueError(
            "AGENT_TEAM_TOKEN is not set. Issue one via POST "
            "/api/v1/admin/teams/{team_key}/tokens and export it."
        )

    host = os.getenv("AGENT_HOST") or str(server["host"])
    port = int(os.getenv("AGENT_PORT") or server["port"])
    public_url = os.getenv("AGENT_PUBLIC_URL") or str(server["public_url"])

    return AgentConfig(
        agent_key=str(agent["key"]).strip().lower(),
        team_key=str(agent["team_key"]).strip().lower(),
        display_name=str(agent["display_name"]),
        description=str(agent.get("description") or ""),
        version=str(agent.get("version") or "0.1.0"),
        permission=str(agent.get("permission") or ""),
        allowed_roles=tuple(agent.get("allowed_roles") or ()),
        skills=tuple(
            SkillConfig(
                id=str(s.get("id") or ""),
                name=str(s.get("name") or ""),
                description=str(s.get("description") or ""),
                tags=tuple(s.get("tags") or ()),
                examples=tuple(s.get("examples") or ()),
            )
            for s in agent.get("skills") or ()
        ),
        prompts=dict(manifest.get("prompts") or {}),
        host=host,
        port=port,
        public_url=public_url,
        aubrey_base_url=(
            os.getenv("AUBREY_BASE_URL") or str(aubrey["base_url"])
        ).rstrip("/"),
        team_token=team_token,
        settings=dict(manifest.get("settings") or {}),
    )
