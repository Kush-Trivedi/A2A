"""The SMS conversation engine — outreach sends and bidirectional replies.

Both directions share the same machinery every other surface uses: a chat
session (channel='sms') per (phone, campaign), the token-budgeted memory
window, the context envelope, and one A2A dispatch to the campaign's
registered agent whose manifest prompt writes the actual words. The
platform holds the Twilio credentials; agents never see them.

Order of gates on OUTBOUND (all mandatory):
    consent (opted_in) → Casbin (sms role vs agent permission) →
    generate via A2A → length cap → send → ledger + session record
Order of gates on INBOUND:
    signature (route) → idempotency → compliance keywords → thread →
    campaign mode → consent → dispatch (background) → reply"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.chat import MessageKind, MessageRole
from ...entity.sms import CampaignMode, ConsentStatus, SmsThreadEntity
from ...security.authorization.enforcer import get_casbin_enforcer
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import AppError, DatabaseError, ForbiddenError
from ..a2a.a2a_client_service import get_a2a_client_service
from ..a2a.context_envelope import ContextEnvelope
from ..chat.memory_window import get_memory_window_builder
from ..chat.session_service import get_chat_session_service
from .campaign_service import get_sms_campaign_service
from .consent_service import KeywordKind, classify_keyword, get_sms_consent_service
from .message_log_service import get_sms_message_log_service
from .sms_settings import get_sms_settings
from .twilio_client import SmsSendError, get_twilio_rest_client

logger = Logger(__name__).get_logger()

_STATUS_CALLBACK_PATH = "/api/v1/sms/webhooks/status"
_SMS_CONTEXT_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class OutreachOutcome:
    phone: str
    outcome: str  # sent | skipped_no_consent | skipped_opted_out | failed
    twilio_sid: str = ""
    detail: str = ""


@dataclass(frozen=True)
class InboundOutcome:
    handled: str  # duplicate | opt_out | opt_in | help | stored | replied | ignored
    reply_sid: str = ""
    detail: str = ""
    background: bool = False
    followup: "InboundFollowup | None" = None


@dataclass(frozen=True)
class InboundFollowup:
    """The slow half of an inbound turn (LLM + send), run AFTER the webhook
    has already answered Twilio with empty TwiML."""

    phone: str
    body: str
    thread_id: str
    campaign_key: str
    agent_key: str
    session_id: str
    extra: dict = field(default_factory=dict)


class SmsChannelService:
    def __init__(self) -> None:
        self._db = get_postgres_connector()
        self._settings = get_sms_settings()
        self._campaigns = get_sms_campaign_service()
        self._consent = get_sms_consent_service()
        self._ledger = get_sms_message_log_service()
        self._twilio = get_twilio_rest_client()
        self._sessions = get_chat_session_service()
        self._windows = get_memory_window_builder()

    # ------------------------------------------------------------------ #
    # Outreach                                                            #
    # ------------------------------------------------------------------ #

    async def send_outreach(
        self, *, campaign_key: str, recipients: list[dict]
    ) -> list[OutreachOutcome]:
        tenant_id = self._settings.tenant_id
        campaign = await self._campaigns.get(tenant_id=tenant_id, key=campaign_key)
        agent = await self._campaigns.active_agent(
            tenant_id=tenant_id, agent_key=campaign.agent_key
        )
        outcomes: list[OutreachOutcome] = []
        for recipient in recipients:
            phone = str(recipient.get("phone") or "").strip()
            context_vars = dict(recipient.get("context") or {})
            if not phone:
                outcomes.append(OutreachOutcome(phone="", outcome="failed", detail="missing phone"))
                continue
            try:
                outcomes.append(
                    await self._outreach_one(
                        tenant_id=tenant_id, campaign=campaign, agent=agent,
                        phone=phone, context_vars=context_vars,
                    )
                )
            except AppError as exc:
                outcomes.append(
                    OutreachOutcome(phone=phone, outcome="failed", detail=exc.client_message())
                )
            except Exception:  # noqa: BLE001 — one bad recipient never stops the batch
                logger.error("Outreach send failed", extra={"phone_suffix": phone[-4:]}, exc_info=True)
                outcomes.append(OutreachOutcome(phone=phone, outcome="failed", detail="internal error"))
        return outcomes

    async def _outreach_one(
        self, *, tenant_id: str, campaign, agent, phone: str, context_vars: dict
    ) -> OutreachOutcome:
        # Gate 1 — consent. No recorded opt-in, no message. Ever.
        status = await self._consent.status_for(tenant_id=tenant_id, phone=phone)
        if status == ConsentStatus.OPTED_OUT:
            return OutreachOutcome(phone=phone, outcome="skipped_opted_out",
                                   detail="recipient has opted out")
        if status != ConsentStatus.OPTED_IN:
            return OutreachOutcome(
                phone=phone, outcome="skipped_no_consent",
                detail="no recorded opt-in — record consent before outreach (TCPA)",
            )

        # Gate 2 — the sms role must permit this agent (same rule as chat).
        await self._enforce_agent(tenant_id=tenant_id, agent=agent)

        thread = await self._thread_for(
            tenant_id=tenant_id, phone=phone,
            campaign_key=campaign.key, agent_key=agent.agent_key,
        )
        prompt_text = self._outreach_text(context_vars)
        answer = await self._generate(
            tenant_id=tenant_id, phone=phone, agent=agent,
            session_id=thread.session_id, text=prompt_text, purpose="sms_outreach",
        )

        result = await self._deliver(
            tenant_id=tenant_id, phone=phone, body=answer,
            campaign_key=campaign.key, agent_key=agent.agent_key,
            session_id=thread.session_id,
        )
        if result.outcome != "sent":
            return result

        context = self._phone_context(tenant_id, phone, thread.session_id)
        await self._sessions.append_message(
            context=context, session_id=thread.session_id,
            role=MessageRole.ASSISTANT, content=answer,
            metadata={"kind": MessageKind.ANSWER, "agent_key": agent.agent_key,
                      "channel": "sms", "campaign_key": campaign.key},
        )
        await self._touch_thread(thread.id, outbound=True)
        return result

    @staticmethod
    def _outreach_text(context_vars: dict) -> str:
        lines = "\n".join(f"{k}: {v}" for k, v in context_vars.items())
        return (
            "Compose the outreach SMS for this recipient.\n"
            f"Recipient context:\n{lines if lines else '(none provided)'}"
        )

    # ------------------------------------------------------------------ #
    # Inbound                                                             #
    # ------------------------------------------------------------------ #

    async def handle_inbound_fast(
        self, *, phone: str, body: str, twilio_sid: str
    ) -> InboundOutcome:
        """The synchronous half — everything compliance-critical happens
        HERE, before Twilio gets its 200: idempotency, opt-out/opt-in/help
        recording, thread resolution. The LLM turn is deferred."""
        tenant_id = self._settings.tenant_id

        if await self._ledger.inbound_exists(twilio_sid=twilio_sid):
            return InboundOutcome(handled="duplicate")

        keyword = classify_keyword(body)
        if keyword == KeywordKind.OPT_OUT:
            await self._consent.record_opt_out(
                tenant_id=tenant_id, phone=phone, source="keyword",
                keyword=body.strip().lower(),
            )
            await self._ledger.record_inbound(
                tenant_id=tenant_id, phone=phone, body=body,
                twilio_sid=twilio_sid, opt_out_type="STOP",
            )
            # Twilio's own filtering auto-replies and blocks — we only record.
            return InboundOutcome(handled="opt_out")
        if keyword == KeywordKind.OPT_IN:
            await self._consent.record_opt_in(
                tenant_id=tenant_id, phone=phone, source="keyword",
                keyword=body.strip().lower(),
            )
            await self._ledger.record_inbound(
                tenant_id=tenant_id, phone=phone, body=body,
                twilio_sid=twilio_sid, opt_out_type="START",
            )
            return InboundOutcome(handled="opt_in")
        if keyword == KeywordKind.HELP:
            await self._ledger.record_inbound(
                tenant_id=tenant_id, phone=phone, body=body,
                twilio_sid=twilio_sid, opt_out_type="HELP",
            )
            return InboundOutcome(handled="help")

        thread = await self._latest_thread(tenant_id=tenant_id, phone=phone)
        if thread is None:
            if not self._settings.default_campaign:
                await self._ledger.record_inbound(
                    tenant_id=tenant_id, phone=phone, body=body, twilio_sid=twilio_sid
                )
                return InboundOutcome(
                    handled="ignored",
                    detail="no thread and no twilio.sms.default_campaign configured",
                )
            campaign = await self._campaigns.get(
                tenant_id=tenant_id, key=self._settings.default_campaign
            )
            agent = await self._campaigns.active_agent(
                tenant_id=tenant_id, agent_key=campaign.agent_key
            )
            thread = await self._thread_for(
                tenant_id=tenant_id, phone=phone,
                campaign_key=campaign.key, agent_key=agent.agent_key,
            )
            # They texted us first — that's consent for this conversation.
            if await self._consent.status_for(tenant_id=tenant_id, phone=phone) is None:
                await self._consent.record_opt_in(
                    tenant_id=tenant_id, phone=phone, source="inbound_first_contact"
                )

        campaign = await self._campaigns.get(tenant_id=tenant_id, key=thread.campaign_key)
        context = self._phone_context(tenant_id, phone, thread.session_id)
        await self._ledger.record_inbound(
            tenant_id=tenant_id, phone=phone, body=body, twilio_sid=twilio_sid,
            campaign_key=campaign.key, agent_key=thread.agent_key,
            session_id=thread.session_id,
        )
        await self._sessions.append_message(
            context=context, session_id=thread.session_id,
            role=MessageRole.USER, content=body,
            metadata={"channel": "sms", "campaign_key": campaign.key},
        )
        await self._touch_thread(thread.id, inbound=True)

        if campaign.mode == CampaignMode.OUTREACH:
            # Outreach-only: stored for the record, never dispatched.
            return InboundOutcome(handled="stored", detail="outreach-only campaign")

        if (
            await self._consent.status_for(tenant_id=tenant_id, phone=phone)
        ) == ConsentStatus.OPTED_OUT:
            return InboundOutcome(handled="stored", detail="sender is opted out")

        return InboundOutcome(
            handled="replied", background=True,
            followup=InboundFollowup(
                phone=phone, body=body, thread_id=thread.id,
                campaign_key=campaign.key, agent_key=thread.agent_key,
                session_id=thread.session_id,
            ),
        )

    async def run_inbound_followup(self, followup: InboundFollowup) -> None:
        """The slow half — LLM turn + reply send. Runs as a background task
        after the webhook already returned; failures land in the ledger and
        the log, never back at Twilio."""
        tenant_id = self._settings.tenant_id
        try:
            agent = await self._campaigns.active_agent(
                tenant_id=tenant_id, agent_key=followup.agent_key
            )
            await self._enforce_agent(tenant_id=tenant_id, agent=agent)
            answer = await self._generate(
                tenant_id=tenant_id, phone=followup.phone, agent=agent,
                session_id=followup.session_id, text=followup.body, purpose="sms",
            )
            result = await self._deliver(
                tenant_id=tenant_id, phone=followup.phone, body=answer,
                campaign_key=followup.campaign_key, agent_key=followup.agent_key,
                session_id=followup.session_id,
            )
            if result.outcome == "sent":
                context = self._phone_context(tenant_id, followup.phone, followup.session_id)
                await self._sessions.append_message(
                    context=context, session_id=followup.session_id,
                    role=MessageRole.ASSISTANT, content=answer,
                    metadata={"kind": MessageKind.ANSWER, "agent_key": followup.agent_key,
                              "channel": "sms", "campaign_key": followup.campaign_key},
                )
                await self._touch_thread(followup.thread_id, outbound=True)
        except Exception:  # noqa: BLE001 — background task: log, never raise
            logger.error(
                "Inbound SMS follow-up failed",
                extra={"phone_suffix": followup.phone[-4:], "campaign": followup.campaign_key},
                exc_info=True,
            )

    # ------------------------------------------------------------------ #
    # Shared machinery                                                    #
    # ------------------------------------------------------------------ #

    async def _generate(
        self, *, tenant_id: str, phone: str, agent, session_id: str, text: str, purpose: str
    ) -> str:
        history = await self._sessions.list_messages(
            context=self._phone_context(tenant_id, phone, session_id),
            session_id=session_id,
        )
        window = self._windows.build(history, window_tokens=self._settings.window_tokens)
        envelope = ContextEnvelope(
            tenant_id=tenant_id,
            user_id=f"sms:{phone}",
            actor_id=f"sms:{phone}",
            roles=(self._settings.user_role,),
            session_id=session_id,
            purpose=purpose,
            window=tuple({"role": w.role, "content": w.content} for w in window),
        )
        chunks: list[str] = []
        async for event in get_a2a_client_service().stream_message(
            agent_key=agent.agent_key, card_url=agent.card_url,
            text=text, context_id=session_id, envelope=envelope,
        ):
            if event.kind == "text" and event.text:
                chunks.append(event.text)
        answer = "".join(chunks).strip()
        if not answer:
            raise ForbiddenError("The campaign agent produced no message.")
        return self._cap_length(answer)

    def _cap_length(self, body: str) -> str:
        cap = self._settings.max_length
        if len(body) <= cap:
            return body
        cut = body[:cap]
        if " " in cut[int(cap * 0.8):]:
            cut = cut[: cut.rindex(" ")]
        logger.warning("SMS body capped", extra={"cap": cap, "original_chars": len(body)})
        return cut

    async def _deliver(
        self, *, tenant_id: str, phone: str, body: str,
        campaign_key: str, agent_key: str, session_id: str,
    ) -> OutreachOutcome:
        # Consent is re-checked at the moment of send — a STOP that arrived
        # while the LLM was generating still wins.
        if not await self._consent.can_send(tenant_id=tenant_id, phone=phone):
            return OutreachOutcome(phone=phone, outcome="skipped_opted_out",
                                   detail="consent lost before send")
        callback = None
        if self._settings.status_callbacks_enabled and self._settings.webhook_base_url:
            callback = f"{self._settings.webhook_base_url}{_STATUS_CALLBACK_PATH}"
        try:
            sent = await self._twilio.send(to=phone, body=body, status_callback=callback)
        except SmsSendError as exc:
            if exc.twilio_code == "21610":
                # Twilio's block list knows something we don't — sync it.
                await self._consent.record_opt_out(
                    tenant_id=tenant_id, phone=phone, source="twilio_block_list",
                    keyword="", note="send rejected with 21610",
                )
            await self._ledger.record_outbound(
                tenant_id=tenant_id, phone=phone, body=body, twilio_sid="",
                status="failed", campaign_key=campaign_key, agent_key=agent_key,
                session_id=session_id, error_code=exc.twilio_code,
            )
            return OutreachOutcome(phone=phone, outcome="failed", detail=exc.client_message())

        await self._ledger.record_outbound(
            tenant_id=tenant_id, phone=phone, body=body, twilio_sid=sent.sid,
            status=sent.status, campaign_key=campaign_key, agent_key=agent_key,
            session_id=session_id, num_segments=sent.num_segments,
            error_code=sent.error_code,
        )
        return OutreachOutcome(phone=phone, outcome="sent", twilio_sid=sent.sid)

    async def _enforce_agent(self, *, tenant_id: str, agent) -> None:
        if not agent.permission:
            return
        allowed = await get_casbin_enforcer().enforce_any_role(
            [self._settings.user_role], tenant_id,
            f"agent:{agent.agent_key}", agent.permission,
        )
        if not allowed:
            raise ForbiddenError(
                f"Role '{self._settings.user_role}' does not permit agent "
                f"'{agent.agent_key}' — add it to the agent manifest's "
                "allowed_roles and restart the agent.",
            )

    def _phone_context(self, tenant_id: str, phone: str, session_id: str) -> SessionContext:
        now = datetime.now(timezone.utc)
        return SessionContext(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=f"sms:{phone}",
            actor_id=f"sms:{phone}",
            email="",
            display_name=f"SMS {phone[-4:]}",
            auth_provider="sms",
            csrf_token="",
            created_at=now,
            last_seen_at=now,
            expires_at=now + _SMS_CONTEXT_TTL,
            roles=(self._settings.user_role,),
        )

    async def _thread_for(
        self, *, tenant_id: str, phone: str, campaign_key: str, agent_key: str
    ) -> SmsThreadEntity:
        try:
            async with self._db.session() as session:
                thread = (
                    await session.exec(
                        select(SmsThreadEntity).where(
                            SmsThreadEntity.tenant_id == tenant_id,
                            SmsThreadEntity.phone == phone,
                            SmsThreadEntity.campaign_key == campaign_key,
                        )
                    )
                ).first()
                if thread is not None:
                    return thread
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

        chat = await self._sessions.create_session(
            context=self._phone_context(tenant_id, phone, session_id=""),
            title=f"sms:{campaign_key}", channel="sms",
        )
        try:
            async with self._db.session() as session:
                thread = SmsThreadEntity(
                    id=uuid.uuid4().hex,
                    tenant_id=tenant_id,
                    phone=phone,
                    campaign_key=campaign_key,
                    agent_key=agent_key,
                    session_id=chat.id,
                )
                session.add(thread)
                return thread
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def _latest_thread(
        self, *, tenant_id: str, phone: str
    ) -> SmsThreadEntity | None:
        try:
            async with self._db.session() as session:
                return (
                    await session.exec(
                        select(SmsThreadEntity)
                        .where(
                            SmsThreadEntity.tenant_id == tenant_id,
                            SmsThreadEntity.phone == phone,
                        )
                        .order_by(SmsThreadEntity.updated_at.desc())  # type: ignore[attr-defined]
                    )
                ).first()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def _touch_thread(
        self, thread_id: str, *, inbound: bool = False, outbound: bool = False
    ) -> None:
        now = datetime.now(timezone.utc)
        try:
            async with self._db.session() as session:
                thread = (
                    await session.exec(
                        select(SmsThreadEntity).where(SmsThreadEntity.id == thread_id)
                    )
                ).first()
                if thread is None:
                    return
                if inbound:
                    thread.last_inbound_at = now
                if outbound:
                    thread.last_outbound_at = now
                thread.updated_at = now
                session.add(thread)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc


_service: SmsChannelService | None = None


def get_sms_channel_service() -> SmsChannelService:
    global _service
    if _service is None:
        _service = SmsChannelService()
    return _service
