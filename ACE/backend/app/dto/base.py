from typing import Generic, TypeVar
from pydantic import BaseModel, ConfigDict

T = TypeVar("T")

class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ApiEnvelope(StrictBaseModel, (Generic[T])):
    data: T | None = None
    success: bool = True
    message: str = ""

class MessageResponse(StrictBaseModel):
    detail: str