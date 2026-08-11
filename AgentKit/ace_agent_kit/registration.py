"""Agent self-registration — the ONE registrar every agent uses.

The agent announces itself to ACE on startup (and from CI if desired) with
its team's registration token. Idempotent: every boot re-registers, version
bumps snapshot automatically, activation stays an ACE-admin step. ACE never
reaches into a team's repo — the arrow always points agent -> ACE, which is
what makes local and cloud identical (only `ace.base_url` differs).
"""

import asyncio
import logging
from typing import Any

import httpx

from .agent_context import AgentContext, PlaceholderPolicy, get_agent_context

logger = logging.getLogger(__name__)


class AgentRegistrar:
    """Builds the registration payload from the manifest (agent.yaml) + the
    env config (config/env/<ENV>.yaml) and registers with ACE."""

    def __init__(self, context: AgentContext | None = None) -> None:
        self._context = context or get_agent_context()

    def _payload(self) -> dict[str, Any]:
        manifest = self._context.manifest
        agent = manifest.get("agent") or {}
        ace = self._context.ace
        llm = self._context.llm
        retrieval = self._context.retrieval
        connections = self._context.connections
        channels = self._context.channels

        team_config: dict[str, Any] = {}
        if llm.get("deployments"):
            team_config["llm_deployments"] = dict(llm["deployments"])
        if connections:
            team_config["connections"] = {
                name: dict(cfg)
                for name, cfg in connections.items()
                if isinstance(cfg, dict) and cfg.get("enabled")
            }
        if channels:
            team_config["channels"] = {
                name: dict(cfg)
                for name, cfg in channels.items()
                if isinstance(cfg, dict)
            }
        auth = self._context.auth
        if auth.get("enabled") and PlaceholderPolicy.is_configured(auth.get("audience")):
            team_config["auth_audience"] = str(auth["audience"])

        return {
            "team_key": str(agent.get("team_key", "")),
            "agent_key": str(agent.get("agent_key", "")),
            "display_name": str(agent.get("display_name", "")),
            "description": str(agent.get("description", "")),
            "card_url": f"{str(ace.get('public_url', '')).rstrip('/')}/.well-known/agent-card.json",
            "version": str(agent.get("version", "0.1.0")),
            "permission": str(ace.get("permission", "chat")),
            "allowed_roles": list(ace.get("allowed_roles") or []),
            "aliases": list(agent.get("aliases") or []),
            "knowledge_sources": list(retrieval.get("knowledge_sources") or []),
            "retrieval_mode": str(retrieval.get("mode") or "sparse"),
            "team_config": team_config,
            "prompts": {
                str(name): {
                    "version": str((entry or {}).get("version", "1.0.0")),
                    "content": str((entry or {}).get("content", "")),
                }
                for name, entry in (manifest.get("prompts") or {}).items()
                if isinstance(entry, dict) and str(entry.get("content", "")).strip()
            },
        }

    async def register(self) -> dict[str, Any]:
        """One registration attempt. Raises on failure."""
        ace = self._context.ace
        base_url = str(ace.get("base_url", "")).rstrip("/")
        token = str(ace.get("registration_token", ""))
        if not PlaceholderPolicy.is_configured(token):
            raise ValueError(
                "ace.registration_token is not configured. Ask an ACE admin to "
                "issue a team token (POST /api/v1/admin/agents/teams/<team>/tokens) "
                "and put it in config/env/<ENV>.yaml (lookup: ref in cloud)."
            )
        async with httpx.AsyncClient(base_url=base_url, timeout=60) as client:
            response = await client.post(
                "/api/v1/agents/register",
                json=self._payload(),
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            body = response.json()
            data = body.get("data") or {}
            overlaps = data.get("route_overlaps") or []
            if overlaps:
                logger.warning(
                    "ACE reported route overlaps — sharpen your skill examples: %s",
                    overlaps,
                )
            logger.info(
                "Registered with ACE: agent=%s status=%s policies=%s routes=%s",
                data.get("agent_key"),
                data.get("status"),
                data.get("policies_seeded"),
                data.get("route_utterances"),
            )
            return data

    async def register_with_retry(
        self, *, attempts: int = 30, delay_seconds: float = 5.0
    ) -> dict[str, Any] | None:
        """Bounded retry for startup: ACE may come up after the agent.
        Never crashes the agent — registration failure is logged, the agent
        still serves its card, and the next boot (or a pipeline run) retries."""
        for attempt in range(1, attempts + 1):
            try:
                return await self.register()
            except ValueError as exc:
                # Misconfiguration (e.g. placeholder registration token):
                # retrying won't help — log LOUDLY, keep serving the card.
                logger.error("ACE registration skipped: %s", exc)
                return None
            except Exception as exc:  # noqa: BLE001 — ACE not up yet
                logger.warning(
                    "ACE registration attempt %s/%s failed: %s",
                    attempt,
                    attempts,
                    exc,
                )
                if attempt < attempts:
                    await asyncio.sleep(delay_seconds)
        logger.error("ACE registration failed after %s attempts.", attempts)
        return None


def register_on_startup(context: AgentContext | None = None) -> "asyncio.Task[Any]":
    """Fire-and-forget startup registration — call from the agent's main
    after the event loop starts."""
    registrar = AgentRegistrar(context)
    return asyncio.get_event_loop().create_task(registrar.register_with_retry())
