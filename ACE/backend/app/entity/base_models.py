from uuid import UUID, uuid4
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, text
from sqlmodel import SQLModel, Field, TIMESTAMP 

class IDModel(SQLModel):
    id: Optional[int] = Field(default=None, primary_key=True, index=True)

class UUIDModel(SQLModel):
    uuid: UUID = Field(
        default_factory=uuid4,
        nullable=False,
        sa_column_kwargs={
            "server_default": text("gen_random_uuid()"),
            "unique": True,
        },
    )

class CreatedAtModel(SQLModel):
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
        sa_type=TIMESTAMP(timezone=True),
    )

class TimestampModel(CreatedAtModel):
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
        sa_type=TIMESTAMP(timezone=True),
        sa_column_kwargs={"onupdate": lambda: datetime.now(timezone.utc)},
    )

