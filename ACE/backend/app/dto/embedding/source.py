from datetime import datetime

from pydantic import Field, field_validator

from ..base import StrictBaseModel

_CHUNKING_STRATEGIES = ("hybrid", "hierarchical", "fixed")
_VECTOR_MODES = ("dense", "sparse", "both")


class IngestChunkingModel(StrictBaseModel):
    strategy: str = "hybrid"
    max_tokens: int = Field(default=512, ge=64, le=4096)
    overlap: int = Field(default=64, ge=0, le=1024)

    @field_validator("strategy")
    @classmethod
    def _known_strategy(cls, value: str) -> str:
        candidate = value.strip().lower()
        if candidate not in _CHUNKING_STRATEGIES:
            raise ValueError(
                f"Unknown chunking strategy '{value}'. Supported: {', '.join(_CHUNKING_STRATEGIES)}."
            )
        return candidate


class IngestEmbeddingModel(StrictBaseModel):
    deployment: str = ""
    vectors: str = "both"

    @field_validator("vectors")
    @classmethod
    def _known_vectors(cls, value: str) -> str:
        candidate = value.strip().lower()
        if candidate not in _VECTOR_MODES:
            raise ValueError(
                f"Unknown vector mode '{value}'. Supported: {', '.join(_VECTOR_MODES)}."
            )
        return candidate


class IngestAccessModel(StrictBaseModel):
    agents: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)


class IngestSourceRequest(StrictBaseModel):
    """The ONE parameterized ingestion request: connection + location +
    chunking + embedding + access. Everything a team decides is a parameter;
    ACE yaml holds none of it."""

    source_name: str = Field(..., min_length=1, max_length=120)
    team_key: str = Field(..., min_length=1, max_length=60)
    connection: str = Field(..., min_length=1, max_length=120)
    location: dict = Field(default_factory=dict)
    chunking: IngestChunkingModel = Field(default_factory=IngestChunkingModel)
    embedding: IngestEmbeddingModel = Field(default_factory=IngestEmbeddingModel)
    access: IngestAccessModel = Field(default_factory=IngestAccessModel)
    description: str = ""


class KnowledgeSourceModel(StrictBaseModel):
    source_name: str
    owner_team_key: str
    connection_name: str
    description: str
    status: str
    location: dict
    chunking: dict
    embedding: dict
    agents: list[str]
    roles: list[str]
    created_at: datetime
    updated_at: datetime
