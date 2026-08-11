from dataclasses import dataclass
from typing import Any

import httpx
from google.protobuf import json_format

from a2a.client import A2ACardResolver, AgentCardResolutionError
from a2a.types import AgentCard

from ...utils.common.logger import Logger
from ...utils.errors import ExternalServiceError, ValidationError
from .a2a_client_service import A2AClientService
from .a2a_settings import A2ASettings, get_a2a_settings

logger = Logger(__name__).get_logger()


@dataclass(frozen=True)
class ValidatedAgentCard:
    name: str
    version: str
    streaming: bool
    skills: tuple[dict[str, Any], ...]
    snapshot: dict[str, Any]

    @property
    def skill_ids(self) -> tuple[str, ...]:
        return tuple(str(skill.get("id", "")) for skill in self.skills)


class AgentCardService:
    """Fetches and validates a team agent's AgentCard.

    Registration is the enforcement point: an unreachable or structurally
    invalid card must fail registration cleanly — a broken agent never
    becomes routable.
    """

    def __init__(self, settings: A2ASettings | None = None) -> None:
        self._settings = settings or get_a2a_settings()

    async def fetch_and_validate(self, card_url: str) -> ValidatedAgentCard:
        card = await self._fetch(card_url)
        problems = self._validate(card)
        if problems:
            raise ValidationError(
                "Agent card failed validation: " + "; ".join(problems),
                details={"card_url": card_url, "problems": problems},
            )
        return self._to_validated(card)

    async def _fetch(self, card_url: str) -> AgentCard:
        base_url, relative_card_path = A2AClientService.split_card_url(card_url)
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.request_timeout_seconds
            ) as client:
                resolver = A2ACardResolver(
                    client, base_url, agent_card_path=relative_card_path
                )
                return await resolver.get_agent_card()
        except AgentCardResolutionError as exc:
            if isinstance(exc.__cause__, httpx.HTTPError):
                raise ExternalServiceError(
                    "Agent card is unreachable.",
                    code="agent_card_unreachable",
                    details={"card_url": card_url},
                    cause=exc,
                ) from exc
            raise ValidationError(
                "The URL did not return a valid A2A agent card.",
                details={"card_url": card_url},
                cause=exc,
            ) from exc
        except json_format.ParseError as exc:
            raise ValidationError(
                "The URL did not return a valid A2A agent card.",
                details={"card_url": card_url},
                cause=exc,
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalServiceError(
                "Agent card is unreachable.",
                code="agent_card_unreachable",
                details={"card_url": card_url},
                cause=exc,
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ExternalServiceError(
                "Agent card fetch failed.",
                code="agent_card_fetch_failed",
                details={"card_url": card_url},
                cause=exc,
            ) from exc

    @staticmethod
    def _validate(card: AgentCard) -> list[str]:
        problems: list[str] = []
        if not card.name.strip():
            problems.append("card has no name")
        if not card.version.strip():
            problems.append("card has no version")
        if not card.skills:
            problems.append("card declares no skills")
        else:
            for index, skill in enumerate(card.skills):
                if not skill.id.strip():
                    problems.append(f"skill[{index}] has no id")
                if not skill.name.strip():
                    problems.append(f"skill[{index}] has no name")
        if not card.supported_interfaces:
            problems.append("card declares no supported interfaces")
        elif not any(iface.url.strip() for iface in card.supported_interfaces):
            problems.append("no supported interface declares a url")
        return problems

    @staticmethod
    def _to_validated(card: AgentCard) -> ValidatedAgentCard:
        skills = tuple(
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "tags": list(skill.tags),
                "examples": list(skill.examples),
            }
            for skill in card.skills
        )
        return ValidatedAgentCard(
            name=card.name,
            version=card.version,
            streaming=bool(card.capabilities.streaming),
            skills=skills,
            snapshot=json_format.MessageToDict(card),
        )


_service: AgentCardService | None = None


def get_agent_card_service() -> AgentCardService:
    global _service
    if _service is None:
        _service = AgentCardService()
    return _service
