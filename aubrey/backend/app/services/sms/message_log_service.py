"""The SMS ledger — every message in or out with its full Twilio
lifecycle. Status callbacks append to `status_history` (never overwrite),
error codes get their human explanation attached, and inbound records are
idempotent on MessageSid so a Twilio webhook retry can never double-reply
(the legacy platform had that bug — the duplicate was caught only AFTER
the reply had been sent).

M10-S1 encryption at rest, applied HERE in the service (entities keep
plain Text columns holding enc:v1: strings): sms_messages.body is
FieldEncryptor ciphertext, and the phone uses the searchable scheme —
stored value encrypted, deterministic HMAC in phone_hash for equality
lookups. Every phone lookup filters phone_hash OR plain phone equality so
legacy plaintext rows (written before M10-S1, no hash) keep matching.
Reads decrypt before returning; the enc:v1: prefix makes old plaintext
rows read back unchanged, and passthrough mode (no key, local dev) keeps
writing plaintext."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import or_
from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.sms import SmsDirection, SmsMessageEntity
from ...utils.common.logger import Logger
from ...utils.crypto import decrypt_or_keep, get_field_encryptor, get_phone_hasher
from ...utils.errors import DatabaseError
from ...utils.telephony import explain_error_code

logger = Logger(__name__).get_logger()


class SmsMessageLogService:
    def __init__(self) -> None:
        self._db = get_postgres_connector()
        self._encryptor = get_field_encryptor()
        self._phones = get_phone_hasher()

    def _phone_filter(self, phone: str):
        """phone_hash equality for M10-S1 rows, plain-phone equality as the
        fallback for legacy rows written before the hash column existed."""
        cleaned = (phone or "").strip()
        return or_(
            SmsMessageEntity.phone_hash == self._phones.hash(cleaned),
            SmsMessageEntity.phone == cleaned,
        )

    async def inbound_exists(self, *, twilio_sid: str) -> bool:
        """Idempotency check — MUST run before any processing/reply."""
        if not twilio_sid:
            return False
        try:
            async with self._db.session() as session:
                row = (
                    await session.exec(
                        select(SmsMessageEntity.id).where(
                            SmsMessageEntity.twilio_sid == twilio_sid,
                            SmsMessageEntity.direction == SmsDirection.INBOUND,
                        )
                    )
                ).first()
                return row is not None
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def record_inbound(
        self,
        *,
        tenant_id: str,
        phone: str,
        body: str,
        twilio_sid: str,
        campaign_key: str = "",
        agent_key: str = "",
        session_id: str = "",
        opt_out_type: str = "",
        num_media: int = 0,
        vendor_details: dict | None = None,
    ) -> SmsMessageEntity:
        return await self._record(
            tenant_id=tenant_id, phone=phone, direction=SmsDirection.INBOUND,
            body=body, twilio_sid=twilio_sid, status="received",
            campaign_key=campaign_key, agent_key=agent_key,
            session_id=session_id, opt_out_type=opt_out_type,
            num_media=num_media, vendor_details=vendor_details,
        )

    async def record_outbound(
        self,
        *,
        tenant_id: str,
        phone: str,
        body: str,
        twilio_sid: str,
        status: str,
        campaign_key: str = "",
        agent_key: str = "",
        session_id: str = "",
        num_segments: int | None = None,
        error_code: str = "",
    ) -> SmsMessageEntity:
        return await self._record(
            tenant_id=tenant_id, phone=phone, direction=SmsDirection.OUTBOUND,
            body=body, twilio_sid=twilio_sid, status=status,
            campaign_key=campaign_key, agent_key=agent_key,
            session_id=session_id, num_segments=num_segments, error_code=error_code,
        )

    async def apply_status_callback(
        self, *, twilio_sid: str, status: str, error_code: str = "", error_message: str = ""
    ) -> bool:
        """Update from a Twilio status callback. Appends to history and
        keeps the latest status; unknown sids are logged, never an error —
        Twilio must always get a 2xx."""
        now = datetime.now(timezone.utc)
        try:
            async with self._db.session() as session:
                message = (
                    await session.exec(
                        select(SmsMessageEntity).where(
                            SmsMessageEntity.twilio_sid == twilio_sid,
                            SmsMessageEntity.direction == SmsDirection.OUTBOUND,
                        )
                    )
                ).first()
                if message is None:
                    logger.warning(
                        "Status callback for unknown message sid",
                        extra={"twilio_sid": twilio_sid, "status": status},
                    )
                    return False
                message.status = status
                if error_code:
                    message.error_code = str(error_code)
                    message.error_explanation = explain_error_code(error_code)
                if error_message:
                    message.error_message = error_message
                message.status_history = list(message.status_history or []) + [
                    {"status": status, "error_code": str(error_code or ""),
                     "error_message": error_message, "at": now.isoformat()}
                ]
                message.updated_at = now
                session.add(message)
                return True
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def count_recent_inbound(
        self, *, tenant_id: str, phone: str, seconds: int
    ) -> int:
        """Inbound volume from one phone in the recent window — the input
        to per-phone rate limiting (SMS loops, abuse)."""
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        try:
            async with self._db.session() as session:
                rows = (
                    await session.exec(
                        select(SmsMessageEntity.id).where(
                            SmsMessageEntity.tenant_id == tenant_id,
                            self._phone_filter(phone),
                            SmsMessageEntity.direction == SmsDirection.INBOUND,
                            SmsMessageEntity.created_at >= cutoff,  # type: ignore[arg-type]
                        )
                    )
                ).all()
                return len(rows)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def list_messages(
        self,
        *,
        tenant_id: str,
        phone: str | None = None,
        campaign_key: str | None = None,
        limit: int = 100,
    ) -> list[SmsMessageEntity]:
        try:
            async with self._db.session() as session:
                statement = select(SmsMessageEntity).where(
                    SmsMessageEntity.tenant_id == tenant_id
                )
                if phone:
                    statement = statement.where(self._phone_filter(phone))
                if campaign_key:
                    statement = statement.where(
                        SmsMessageEntity.campaign_key == campaign_key.strip().lower()
                    )
                statement = statement.order_by(
                    SmsMessageEntity.created_at.desc()  # type: ignore[attr-defined]
                ).limit(max(1, min(limit, 500)))
                rows = list((await session.exec(statement)).all())
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        # Decrypt on detached rows for the admin audit surface; legacy
        # plaintext rows pass through unchanged.
        for row in rows:
            row.body = decrypt_or_keep(self._encryptor, row.body)
            row.phone = decrypt_or_keep(self._encryptor, row.phone)
        return rows

    async def _record(
        self,
        *,
        tenant_id: str,
        phone: str,
        direction: str,
        body: str,
        twilio_sid: str,
        status: str,
        campaign_key: str = "",
        agent_key: str = "",
        session_id: str = "",
        num_segments: int | None = None,
        error_code: str = "",
        opt_out_type: str = "",
        num_media: int = 0,
        vendor_details: dict | None = None,
    ) -> SmsMessageEntity:
        now = datetime.now(timezone.utc)
        cleaned = (phone or "").strip()
        try:
            async with self._db.session() as session:
                message = SmsMessageEntity(
                    id=uuid.uuid4().hex,
                    tenant_id=tenant_id,
                    # Searchable encryption (M10-S1): ciphertext in phone,
                    # deterministic HMAC in phone_hash for lookups.
                    phone=self._encryptor.encrypt(cleaned),
                    phone_hash=self._phones.hash(cleaned),
                    direction=direction,
                    campaign_key=campaign_key,
                    agent_key=agent_key,
                    session_id=session_id,
                    twilio_sid=twilio_sid,
                    body=self._encryptor.encrypt(body),
                    status=status,
                    error_code=str(error_code or ""),
                    error_explanation=explain_error_code(error_code),
                    num_segments=num_segments,
                    num_media=num_media,
                    vendor_details=dict(vendor_details or {}),
                    opt_out_type=opt_out_type,
                    status_history=[
                        {"status": status, "error_code": str(error_code or ""), "at": now.isoformat()}
                    ],
                )
                session.add(message)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        # Detached after commit — the caller keeps seeing plaintext.
        message.phone = cleaned
        message.body = body
        return message


_service: SmsMessageLogService | None = None


def get_sms_message_log_service() -> SmsMessageLogService:
    global _service
    if _service is None:
        _service = SmsMessageLogService()
    return _service
