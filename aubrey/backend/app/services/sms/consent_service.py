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
  opt-in (admin-recorded from a signed form, or a prior START)."""

import uuid
from datetime import datetime, timezone

from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.sms import ConsentStatus, SmsConsentEntity
from ...utils.common.logger import Logger
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
        return await self._transition(
            tenant_id=tenant_id, phone=phone, status=ConsentStatus.OPTED_OUT,
            source=source, keyword=keyword, note=note,
        )

    async def _get(self, tenant_id: str, phone: str) -> SmsConsentEntity | None:
        try:
            async with self._db.session() as session:
                return (
                    await session.exec(
                        select(SmsConsentEntity).where(
                            SmsConsentEntity.tenant_id == tenant_id,
                            SmsConsentEntity.phone == phone,
                        )
                    )
                ).first()
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

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
        try:
            async with self._db.session() as session:
                record = (
                    await session.exec(
                        select(SmsConsentEntity).where(
                            SmsConsentEntity.tenant_id == tenant_id,
                            SmsConsentEntity.phone == cleaned,
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
                return record
        except ValidationError:
            raise
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc


_service: SmsConsentService | None = None


def get_sms_consent_service() -> SmsConsentService:
    global _service
    if _service is None:
        _service = SmsConsentService()
    return _service
