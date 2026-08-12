"""This agent's A2A AgentCard, built from the team manifest. Security
schemes join the card at M5 (authenticated hops)."""

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from .config import AgentConfig


class AgentCardBuilder:
    def __init__(self, config: AgentConfig) -> None:
        self._config = config

    def build(self) -> AgentCard:
        return AgentCard(
            name=self._config.display_name,
            description=self._config.description,
            version=self._config.version,
            supported_interfaces=[
                AgentInterface(
                    url=self._config.public_url, protocol_binding="JSONRPC"
                ),
            ],
            capabilities=AgentCapabilities(streaming=True),
            default_input_modes=["text/plain"],
            default_output_modes=["text/plain"],
            skills=[
                AgentSkill(
                    id=skill.id,
                    name=skill.name,
                    description=skill.description,
                    tags=list(skill.tags),
                    examples=list(skill.examples),
                )
                for skill in self._config.skills
            ],
        )
