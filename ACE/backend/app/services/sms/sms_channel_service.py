import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from ...config.application_context import get_application_context
from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.sms import SmsConversationEntity, SmsConversationStatus, SmsMessageEntity
from ...security.field_encryptor import FieldEncryptor, get_field_encryptor
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError
from ..conversation.conversation_service import ConversationService, get_conversation_service
from .twilio_settings import TwilioSettings, get_twilio_settings
from .twilio_sms_client import TwilioSmsClient, get_twilio_sms_client

logger = Logger(__name__).get_logger()

_OPT_OUT_REPLY = ""  # regulatory: no reply after opt-out
_OPT_IN_REPLY = "You are opted back in. How can we help?"


@dataclass(frozen=True)
class InboundSmsResult:
    conversation_id: str
    status: str
    replied: bool


class SmsChannelService:
    """Channel-agnostic SMS bridge.

    Inbound texts become normal ACE chat turns: each phone number maps to one
    conversation bound to an ACE chat session and an agent_key — the SAME A2A
    agents answer over SMS as on the web chat, so a patient can text about any
    team's topic and only the conversation's agent changes. Bodies and phone
    numbers are encrypted at rest; opt-out honored before anything else.
    """

    def __init__(
        self,
        settings: TwilioSettings | None = None,
        twilio: TwilioSmsClient | None = None,
        conversations: ConversationService | None = None,
        encryptor: FieldEncryptor | None = None,
    ) -> None:
        self._settings = settings or get_twilio_settings()
        self._twilio = twilio or get_twilio_sms_client()
        self._chat = conversations or get_conversation_service()
        self._crypto = encryptor or get_field_encryptor()
        self._connector = get_postgres_connector()

    def _phone_hash(self, tenant_id: str, phone: str) -> str:
        pepper = str(
            get_application_context().security.get("identity_hash_pepper") or "ace-sms"
        )
        return hmac.new(
            pepper.encode(), f"{tenant_id}|sms|{phone}".encode(), hashlib.sha256
        ).hexdigest()

    def _sms_context(self, conversation: SmsConversationEntity) -> SessionContext:
        now = datetime.now(timezone.utc)
        return SessionContext(
            session_id=f"sms-{conversation.id}",
            tenant_id=conversation.tenant_id,
            user_id=f"sms:{conversation.from_number_hash[:12]}",
            actor_id=f"sms:{conversation.from_number_hash[:12]}",
            email="",
            display_name="SMS Patient",
            auth_provider="sms",
            csrf_token="",
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(minutes=10),
            roles=self._settings.inbound_roles,
        )

    async def handle_inbound(
        self,
        *,
        tenant_id: str,
        from_number: str,
        to_number: str,
        body: str,
        message_sid: str,
        agent_key: str | None = None,
    ) -> InboundSmsResult:
        text = (body or "").strip()
        conversation = await self._find_or_create(
            tenant_id=tenant_id,
            from_number=from_number,
            to_number=to_number,
            agent_key=agent_key,
        )
        await self._store_message(conversation, "inbound", text, message_sid)

        lowered = text.lower()
        if lowered in self._settings.opt_out_keywords:
            await self._set_status(conversation.id, SmsConversationStatus.OPTED_OUT)
            return InboundSmsResult(conversation.id, SmsConversationStatus.OPTED_OUT, False)
        if conversation.status == SmsConversationStatus.OPTED_OUT:
            if lowered in self._settings.opt_in_keywords:
                await self._set_status(conversation.id, SmsConversationStatus.ACTIVE)
                await self._reply(conversation, _OPT_IN_REPLY)
                return InboundSmsResult(conversation.id, SmsConversationStatus.ACTIVE, True)
            return InboundSmsResult(conversation.id, SmsConversationStatus.OPTED_OUT, False)

        # Route through the normal chat pipeline -> the conversation's agent.
        context = self._sms_context(conversation)
        result = await self._chat.send(
            context=context,
            agent_id=conversation.agent_key,
            message=text,
            session_id=conversation.chat_session_id,
        )
        if conversation.chat_session_id != result.session_id:
            await self._bind_session(conversation.id, result.session_id)

        await self._reply(conversation, result.answer)
        return InboundSmsResult(conversation.id, conversation.status, True)

    async def send_outreach(
        self, *, tenant_id: str, to_number: str, body: str, agent_key: str
    ) -> str:
        """One-way outreach (team agents via the capability API)."""
        conversation = await self._find_or_create(
            tenant_id=tenant_id,
            from_number=to_number,
            to_number=self._settings.outbound_number,
            agent_key=agent_key,
        )
        if conversation.status == SmsConversationStatus.OPTED_OUT:
            raise DatabaseError(  # surfaced as a clean failure without sending
                cause=RuntimeError("Recipient has opted out of SMS.")
            )
        sid = await self._twilio.send_text_message(to_number=to_number, body=body)
        await self._store_message(conversation, "outbound", body, sid)
        logger.info(
            "Outreach SMS sent",
            extra={"conversation_id": conversation.id, "agent_key": agent_key},
        )
        return sid

    async def _reply(self, conversation: SmsConversationEntity, body: str) -> None:
        if not body.strip():
            return
        status_callback = (
            f"{self._settings.webhook_base_url}/api/v1/channels/sms/status"
            if self._settings.webhook_base_url
            and not self._settings.webhook_base_url.startswith("your_")
            else None
        )
        sid = await self._twilio.send_text_message(
            to_number=self._crypto.decrypt(conversation.from_number) or "",
            body=body,
            status_callback=status_callback,
        )
        await self._store_message(conversation, "outbound", body, sid)

    async def _find_or_create(
        self, *, tenant_id: str, from_number: str, to_number: str, agent_key: str | None
    ) -> SmsConversationEntity:
        phone_hash = self._phone_hash(tenant_id, from_number)
        try:
            async with self._connector.session() as session:
                existing = (
                    await session.exec(
                        select(SmsConversationEntity)
                        .where(
                            SmsConversationEntity.tenant_id == tenant_id,
                            SmsConversationEntity.from_number_hash == phone_hash,
                        )
                        .order_by(SmsConversationEntity.created_at.desc())  # type: ignore[union-attr]
                    )
                ).first()
                if existing is not None:
                    if agent_key and existing.agent_key != agent_key:
                        existing.agent_key = agent_key  # same patient, new team topic
                        session.add(existing)
                    return existing
                now = datetime.now(timezone.utc)
                conversation = SmsConversationEntity(
                    id=uuid.uuid4().hex,
                    tenant_id=tenant_id,
                    from_number_hash=phone_hash,
                    from_number=self._crypto.encrypt(from_number) or from_number,
                    to_number=self._crypto.encrypt(to_number) or to_number,
                    status=SmsConversationStatus.ACTIVE,
                    agent_key=agent_key or self._settings.default_agent,
                    created_at=now,
                    updated_at=now,
                )
                session.add(conversation)
                return conversation
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def _store_message(
        self,
        conversation: SmsConversationEntity,
        direction: str,
        body: str,
        message_sid: str | None,
    ) -> None:
        now = datetime.now(timezone.utc)
        entity = SmsMessageEntity(
            id=uuid.uuid4().hex,
            tenant_id=conversation.tenant_id,
            conversation_id=conversation.id,
            direction=direction,
            message_body=self._crypto.encrypt(body) or body,
            twilio_message_sid=message_sid,
            created_at=now,
            updated_at=now,
        )
        try:
            async with self._connector.session() as session:
                session.add(entity)
        except Exception:  # noqa: BLE001 — duplicate sid (webhook retry) is fine
            logger.info("SMS message store skipped (likely duplicate sid)")

    async def update_delivery_status(self, *, message_sid: str, status: str) -> None:
        """Persist Twilio delivery-status callbacks onto the stored message."""
        if not message_sid:
            return
        try:
            async with self._connector.session() as session:
                message = (
                    await session.exec(
                        select(SmsMessageEntity).where(
                            SmsMessageEntity.twilio_message_sid == message_sid
                        )
                    )
                ).first()
                if message is not None:
                    message.delivery_status = status
                    message.updated_at = datetime.now(timezone.utc)
                    session.add(message)
        except Exception:  # noqa: BLE001 — status updates must never 500 Twilio
            logger.error("Delivery status update failed", extra={"sid": message_sid}, exc_info=True)

    async def _set_status(self, conversation_id: str, status: str) -> None:
        async with self._connector.session() as session:
            conversation = (
                await session.exec(
                    select(SmsConversationEntity).where(
                        SmsConversationEntity.id == conversation_id
                    )
                )
            ).one()
            conversation.status = status
            conversation.updated_at = datetime.now(timezone.utc)
            session.add(conversation)

    async def _bind_session(self, conversation_id: str, chat_session_id: str) -> None:
        async with self._connector.session() as session:
            conversation = (
                await session.exec(
                    select(SmsConversationEntity).where(
                        SmsConversationEntity.id == conversation_id
                    )
                )
            ).one()
            conversation.chat_session_id = chat_session_id
            conversation.updated_at = datetime.now(timezone.utc)
            session.add(conversation)


_service: SmsChannelService | None = None


def get_sms_channel_service() -> SmsChannelService:
    global _service
    if _service is None:
        _service = SmsChannelService()
    return _service
