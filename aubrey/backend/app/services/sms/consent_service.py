"""Opt-in / opt-out — the FIRST gate on every SMS in either direction.

Rules encoded here (carrier + FCC + TCPA):
- Opt-out keywords (incl. REVOKE/OPTOUT added by the FCC's 2025 ruling)
  are honored immediately, before anything else looks at the message.
- Keyword matching is exact whole-body match after trim/lowercase — the
  same rule Twilio's own filtering applies.
- Twilio maintains its own block list and auto-replies to STOP/START/HELP,
  so we never send our own keyword responses (that would double-text) —
  we RECORD the transition and enforce it on every future outbound.
- Consent transitions are append-only history: in TCPA disputes the burden
  of proof is on the sender, so every change keeps at/from/to/source.
- No consent record = NO outbound. Outreach requires an explicit recorded
  opt-in (admin-recorded from a signed form, or a prior START).

M10-S1 searchable encryption, applied HERE in the service (entity keeps a
plain Text column holding enc:v1: strings): the stored phone value is
FieldEncryptor ciphertext with a deterministic HMAC in phone_hash for
equality lookups. Every lookup filters phone_hash OR plain-phone equality
so legacy plaintext rows (no hash) keep matching, and a transition on a
legacy row re-writes it into the encrypted+hashed form. The enc:v1:
prefix makes old plaintext rows read back unchanged; passthrough mode
(no key, local dev) keeps writing plaintext.

M10c §8.3 consent-bound lifecycle: an opt-out cancels the subject's open
prospective memories — a STOP must stop future follow-ups too."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import or_
from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.sms import ConsentStatus, SmsConsentEntity
from ...utils.common.logger import Logger
from ...utils.crypto import decrypt_or_keep, get_field_encryptor, get_phone_hasher
from ...utils.errors import DatabaseError, ValidationError

logger = Logger(__name__).get_logger()

OPT_OUT_KEYWORDS = frozenset(
    {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "revoke", "optout"}
)
OPT_IN_KEYWORDS = frozenset({"start", "unstop", "yes"})
HELP_KEYWORDS = frozenset({"help", "info"})


class KeywordKind:
    OPT_OUT = "opt_out"
    OPT_IN = "opt_in"
    HELP = "help"


def classify_keyword(body: str) -> str | None:
    """Exact whole-body match after trim/lowercase — 'STOP please' is a
    normal message, 'STOP' is a compliance keyword."""
    normalized = (body or "").strip().lower()
    if normalized in OPT_OUT_KEYWORDS:
        return KeywordKind.OPT_OUT
    if normalized in OPT_IN_KEYWORDS:
        return KeywordKind.OPT_IN
    if normalized in HELP_KEYWORDS:
        return KeywordKind.HELP
    return None


class SmsConsentService:
    def __init__(self) -> None:
        self._db = get_postgres_connector()
        self._encryptor = get_field_encryptor()
        self._phones = get_phone_hasher()

    async def status_for(self, *, tenant_id: str, phone: str) -> str | None:
        record = await self._get(tenant_id, phone)
        return record.status if record is not None else None

    async def can_send(self, *, tenant_id: str, phone: str) -> bool:
        """The outbound gate: only an explicit recorded opt-in passes."""
        return (await self.status_for(tenant_id=tenant_id, phone=phone)) == ConsentStatus.OPTED_IN

    async def record_opt_in(
        self, *, tenant_id: str, phone: str, source: str, keyword: str = "", note: str = ""
    ) -> SmsConsentEntity:
        return await self._transition(
            tenant_id=tenant_id, phone=phone, status=ConsentStatus.OPTED_IN,
            source=source, keyword=keyword, note=note,
        )

    async def record_opt_out(
        self, *, tenant_id: str, phone: str, source: str, keyword: str = "", note: str = ""
    ) -> SmsConsentEntity:
        record = await self._transition(
            tenant_id=tenant_id, phone=phone, status=ConsentStatus.OPTED_OUT,
            source=source, keyword=keyword, note=note,
        )
        # §8.3 consent-bound lifecycle: STOP cancels the subject's open
        # prospective memories. Lazy import (retention -> memory would cycle
        # at module scope) and never fatal — the opt-out itself is already
        # recorded and must stand regardless.
        try:
            from ..retention.retention_service import get_retention_service

            await get_retention_service().cancel_prospects_for_subject(
                tenant_id=tenant_id, user_id=f"sms:{(phone or '').strip()}"
            )
        except Exception:  # noqa: BLE001
            logger.error(
                "Prospect cancellation after opt-out failed",
                extra={"phone_suffix": (phone or '').strip()[-4:]},
                exc_info=True,
            )
        return record

    async def _get(self, tenant_id: str, phone: str) -> SmsConsentEntity | None:
        cleaned = (phone or "").strip()
        try:
            async with self._db.session() as session:
                record = (
                    await session.exec(
                        select(SmsConsentEntity).where(
                            SmsConsentEntity.tenant_id == tenant_id,
                            or_(
                                SmsConsentEntity.phone_hash == self._phones.hash(cleaned),
                                SmsConsentEntity.phone == cleaned,
                            ),
                        )
                    )
                ).first()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        if record is not None:
            # Detached after the session — display value only, never flushed.
            record.phone = decrypt_or_keep(self._encryptor, record.phone)
        return record

    async def _transition(
        self, *, tenant_id: str, phone: str, status: str, source: str, keyword: str, note: str
    ) -> SmsConsentEntity:
        cleaned = (phone or "").strip()
        if not cleaned.startswith("+") or len(cleaned) < 8:
            raise ValidationError(
                "Phone numbers must be E.164 (e.g. +15551234567).",
                details={"phone": cleaned},
            )
        now = datetime.now(timezone.utc)
        phone_hash = self._phones.hash(cleaned)
        try:
            async with self._db.session() as session:
                record = (
                    await session.exec(
                        select(SmsConsentEntity).where(
                            SmsConsentEntity.tenant_id == tenant_id,
                            or_(
                                SmsConsentEntity.phone_hash == phone_hash,
                                SmsConsentEntity.phone == cleaned,
                            ),
                        )
                    )
                ).first()
                previous = record.status if record is not None else None
                if record is None:
                    record = SmsConsentEntity(
                        id=uuid.uuid4().hex,
                        tenant_id=tenant_id,
                        phone=cleaned,
                        status=status,
                        source=source,
                        keyword=keyword,
                        note=note,
                        history=[],
                    )
                # Every transition (re-)writes the searchable-encryption
                # pair — legacy plaintext rows upgrade on their next write.
                record.phone = self._encryptor.encrypt(cleaned)
                record.phone_hash = phone_hash
                record.status = status
                record.source = source
                record.keyword = keyword
                if note:
                    record.note = note
                if status == ConsentStatus.OPTED_IN:
                    record.opted_in_at = now
                else:
                    record.opted_out_at = now
                record.history = list(record.history or []) + [
                    {
                        "at": now.isoformat(),
                        "from": previous,
                        "to": status,
                        "source": source,
                        "keyword": keyword,
                    }
                ]
                record.updated_at = now
                session.add(record)
                logger.info(
                    "SMS consent transition",
                    extra={
                        "phone_suffix": cleaned[-4:], "from": previous,
                        "to": status, "source": source,
                    },
                )
        except ValidationError:
            raise
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        # Detached after commit — the caller keeps seeing plaintext.
        record.phone = cleaned
        return record


_service: SmsConsentService | None = None


def get_sms_consent_service() -> SmsConsentService:
    global _service
    if _service is None:
        _service = SmsConsentService()
    return _service
