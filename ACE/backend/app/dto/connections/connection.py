from datetime import datetime

from pydantic import field_validator

from ..base import StrictBaseModel
from ...entity.connections import CONNECTION_TYPES

_NAME_MAX = 120


class RegisterConnectionRequest(StrictBaseModel):
    team_key: str
    name: str
    connection_type: str
    description: str = ""
    config: dict = {}
    secrets: dict = {}

    @field_validator("name")
    @classmethod
    def _name_slug(cls, value: str) -> str:
        candidate = value.strip().lower()
        if not candidate or len(candidate) > _NAME_MAX:
            raise ValueError("Connection name must be 1-120 characters.")
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
        if not set(candidate) <= allowed:
            raise ValueError(
                "Connection name may contain only lowercase letters, digits, '_' and '-'."
            )
        return candidate

    @field_validator("connection_type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        candidate = value.strip().lower()
        if candidate not in CONNECTION_TYPES:
            raise ValueError(
                f"Unknown connection type '{value}'. Supported: {', '.join(CONNECTION_TYPES)}."
            )
        return candidate


class ConnectionModel(StrictBaseModel):
    id: str
    team_key: str
    name: str
    connection_type: str
    description: str
    status: str
    config: dict
    secret_keys: list[str]
    created_at: datetime
    updated_at: datetime


class ConnectionHealthModel(StrictBaseModel):
    name: str
    connection_type: str
    status: str  # ok | error | not_configured
    detail: str = ""
