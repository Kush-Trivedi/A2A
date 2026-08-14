from sqlalchemy import Column, Index, Text, UniqueConstraint
from sqlmodel import Field

from backend.app.entity.base_models import TimestampModel


class CampaignMode:
    """Directionality of a campaign — the flag the user-facing behavior
    hangs on. OUTREACH: we send; replies are stored for the record but
    never dispatched to the agent and never answered. BIDIRECTIONAL:
    replies continue the conversation with the campaign's agent."""

    OUTREACH = "outreach"
    BIDIRECTIONAL = "bidirectional"

    ALL = (OUTREACH, BIDIRECTIONAL)


class SmsCampaignEntity(TimestampModel, table=True):
    """A named SMS program (blood-pressure outreach, payment reminders, ...)
    bound to the registered agent whose manifest prompt writes its
    messages. The campaign is the unit teams register; the agent is just
    its voice."""

    __tablename__ = "sms_campaigns"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_sms_campaigns_tenant_key"),
        Index("idx_sms_campaigns_tenant_agent", "tenant_id", "agent_key"),
    )

    id: str = Field(sa_column=Column(Text, primary_key=True))
    tenant_id: str = Field(sa_column=Column(Text, nullable=False))
    key: str = Field(sa_column=Column(Text, nullable=False))
    agent_key: str = Field(sa_column=Column(Text, nullable=False))
    mode: str = Field(sa_column=Column(Text, nullable=False))  # outreach | bidirectional
    description: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
