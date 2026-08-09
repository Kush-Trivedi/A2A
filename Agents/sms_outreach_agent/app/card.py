from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    SecurityRequirement,
    SecurityScheme,
)

from .config import AgentConfig, get_agent_config

_ENTRA_SCHEME_NAME = "entra"


class AgentCardBuilder:
    """Builds this agent's AgentCard from the team-owned manifest.

    When auth is enabled the card declares an OpenID Connect security scheme
    pointing at the Entra tenant, and callers (ACE's AuthInterceptor) attach
    bearer tokens automatically because the card says so — behavior is driven
    by the card, not the environment.
    """

    def __init__(self, config: AgentConfig | None = None) -> None:
        self._config = config or get_agent_config()

    def build(self) -> AgentCard:
        card = AgentCard(
            name=self._config.display_name,
            description=self._config.description,
            version=self._config.version,
            supported_interfaces=[
                AgentInterface(url=self._config.public_url, protocol_binding="JSONRPC"),
            ],
            capabilities=AgentCapabilities(streaming=True),
            default_input_modes=["text/plain"],
            default_output_modes=["text/plain"],
            skills=self._skills(),
        )
        if self._config.auth.enabled:
            self._apply_security(card)
        return card

    def _skills(self) -> list[AgentSkill]:
        return [
            AgentSkill(
                id=skill.id,
                name=skill.name,
                description=skill.description,
                tags=list(skill.tags),
                examples=list(skill.examples),
            )
            for skill in self._config.skills
        ]

    def _apply_security(self, card: AgentCard) -> None:
        scheme = SecurityScheme()
        scheme.open_id_connect_security_scheme.description = (
            "Entra service-plane authentication."
        )
        scheme.open_id_connect_security_scheme.open_id_connect_url = (
            self._config.auth.openid_configuration_url
        )
        card.security_schemes[_ENTRA_SCHEME_NAME].CopyFrom(scheme)

        requirement = SecurityRequirement()
        requirement.schemes[_ENTRA_SCHEME_NAME].SetInParent()
        card.security_requirements.append(requirement)
