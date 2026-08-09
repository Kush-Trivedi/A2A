from sqlmodel import Field
from sqlalchemy import Column, String, Index
from backend.app.entity.base_models import IDModel

class CasbinRuleEntity(IDModel, table=True):
    __tablename__ = "casbin_rule"
    __table_args__ = (
        Index("idx_casbin_rule_ptype", "ptype"),
        Index("idx_casbin_rule_v0", "v0"),
    )

    ptype: str = Field(sa_column=Column(String, nullable=False))
    v0: str | None = Field(default=None, sa_column=Column(String(255), nullable=True))
    v1: str | None = Field(default=None, sa_column=Column(String(255), nullable=True))
    v2: str | None = Field(default=None, sa_column=Column(String(255), nullable=True))
    v3: str | None = Field(default=None, sa_column=Column(String(255), nullable=True))
    v4: str | None = Field(default=None, sa_column=Column(String(255), nullable=True))
    v5: str | None = Field(default=None, sa_column=Column(String(255), nullable=True))