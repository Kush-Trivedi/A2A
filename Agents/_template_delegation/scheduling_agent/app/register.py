"""Self-registration with the ACE agent registry.

Run once after deploying (or whenever agent.yaml changes):

    uv run python -m app.register --cookie "<ace_session_cookie>" --csrf "<csrf_token>"

Registration is the automation point: ACE validates the agent card, stores the
agent row (including the team-owned data config and auth audience), and seeds
the Casbin policies for `allowed_roles`.
"""

import argparse
import asyncio
from typing import Any

import httpx

from .config import AgentConfig, get_agent_config


class AceRegistrationClient:
    """Registers this agent (and its team) with the ACE control plane."""

    def __init__(self, config: AgentConfig | None = None, *, cookie: str, csrf: str) -> None:
        self._config = config or get_agent_config()
        self._headers = {"Cookie": cookie, "X-CSRF-Token": csrf}

    def _team_payload(self) -> dict[str, Any]:
        return {
            "key": self._config.team_key,
            "name": self._config.team_key.replace("_", " ").title(),
            "description": f"ODT team owning the {self._config.display_name}.",
        }

    def _team_config(self) -> dict[str, Any]:
        team_config = dict(self._config.data)
        if self._config.auth.enabled and self._config.auth.audience:
            team_config["auth_audience"] = self._config.auth.audience
        if self._config.llm_deployments:
            team_config["llm_deployments"] = dict(self._config.llm_deployments)
        return team_config

    def _agent_payload(self) -> dict[str, Any]:
        return {
            "team_key": self._config.team_key,
            "agent_key": self._config.agent_key,
            "display_name": self._config.display_name,
            "description": self._config.description,
            "card_url": f"{self._config.public_url}/.well-known/agent-card.json",
            "version": self._config.version,
            "permission": self._config.permission,
            "allowed_roles": list(self._config.allowed_roles),
            "knowledge_sources": list(self._config.knowledge_sources),
            "retrieval_mode": self._config.retrieval_mode,
            "team_config": self._team_config(),
            "prompts": self._config.prompt_store.to_registration_payload(),
        }

    async def register(self) -> None:
        async with httpx.AsyncClient(
            base_url=self._config.ace_base_url, headers=self._headers
        ) as client:
            team_response = await client.post(
                "/api/v1/admin/agents/teams", json=self._team_payload()
            )
            team_response.raise_for_status()
            print("team:", team_response.json().get("message"))

            agent_response = await client.post(
                "/api/v1/admin/agents", json=self._agent_payload()
            )
            agent_response.raise_for_status()
            body = agent_response.json()
            print("agent:", body.get("message"))
            print("policies seeded:", body["data"]["policies_seeded"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Register this agent with ACE.")
    parser.add_argument("--cookie", required=True, help="ACE session cookie header value")
    parser.add_argument("--csrf", required=True, help="ACE CSRF token")
    args = parser.parse_args()
    client = AceRegistrationClient(cookie=args.cookie, csrf=args.csrf)
    asyncio.run(client.register())


if __name__ == "__main__":
    main()
