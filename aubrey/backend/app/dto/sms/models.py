from datetime import datetime
from typing import Any

from ..base import StrictBaseModel


class RegisterCampaignRequest(StrictBaseModel):
    key: str
    agent_key: str
    mode: str  # outreach | bidirectional
    description: str = ""


class CampaignModel(StrictBaseModel):
    id: str
    key: str
    agent_key: str
    mode: str
    description: str
    created_at: datetime
    updated_at: datetime


class ConsentRequest(StrictBaseModel):
    phone: str          # E.164
    status: str         # opted_in | opted_out
    note: str = ""      # where/how consent was captured (form id, call ref, ...)


class ConsentModel(StrictBaseModel):
    phone: str
    status: str
    source: str
    keyword: str
    note: str
    opted_in_at: datetime | None
    opted_out_at: datetime | None
    history: list[Any]


class OutreachRecipient(StrictBaseModel):
    phone: str                      # E.164
    context: dict[str, str] = {}    # facts the agent grounds the message on


class OutreachRequest(StrictBaseModel):
    campaign_key: str
    recipients: list[OutreachRecipient]


class OutreachResultModel(StrictBaseModel):
    phone: str
    outcome: str  # sent | skipped_no_consent | skipped_opted_out | failed
    twilio_sid: str
    detail: str


class OutreachResponse(StrictBaseModel):
    campaign_key: str
    results: list[OutreachResultModel]


class SmsMessageModel(StrictBaseModel):
    id: str
    phone: str
    direction: str
    campaign_key: str
    agent_key: str
    session_id: str
    twilio_sid: str
    body: str
    status: str
    error_code: str
    error_explanation: str
    num_segments: int | None
    opt_out_type: str
    status_history: list[Any]
    created_at: datetime
