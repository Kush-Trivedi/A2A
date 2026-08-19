"""Retention + right-to-erasure (NEW_PLAN §9.5, §8.3 — M10-S2). Before
this service, "archive" never deleted anything and uploaded document text
was permanent; now data has clocks, and every purge writes a
deletion-evidence row (metadata only, never content or raw identifiers) —
the auditable answer to a HIPAA/GDPR review.

Policies are yaml-owned under a TOP-LEVEL `retention:` section:

    retention:
      archived_session_days: 30      # archived chat_sessions -> hard DELETE
      sms_message_days: 365          # sms bodies + vendor_details overwritten
      genie_conversation_days: 90    # genie thread mappings deleted

A value of 0 (or an absent key) disables that policy — retention never
guesses. The section is read straight from the env yaml file:
ApplicationContext exposes only its fixed sections, and these are plain
integers with no secret/lookup semantics, so a direct parse keeps the
config surface additive without touching the context (env-var/lookup:
overrides do not apply here — by design, documented).

What each sweep does:
- chat_sessions: hard DELETE of sessions archived longer than the window.
  The FK graph does the reaping: chat_messages, message_edit_chains(+
  versions), message_feedback, session_documents, and sms_threads all
  cascade. Orphaned session_summaries rows for the deleted sessions are
  removed explicitly (no FK there).
- sms_messages: bodies + vendor_details OVERWRITTEN in place; the row,
  status, and status_history stay — the delivery ledger remains auditable
  (TCPA) while message content gets a clock.
- genie_conversations: whole rows deleted (thread mappings, no content).
- external memory (§8.3): facts/episodes for external subjects (sms:/
  voice: user ids) older than agents.memory.external.retention_days are
  deleted — the consent-bound countdown.

erase_external_subject() is the right-to-erasure path for one campaign
recipient: memory facts/episodes deleted, prospects cancelled AND their
content blanked, the subject's SMS thread sessions hard-deleted (cascade),
message bodies overwritten, session summaries removed — each step
evidenced. Evidence targets use the phone's HMAC token, never the raw
number."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import yaml
from sqlalchemy import text as sql_text

from ...config.application_context import get_application_context
from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.memory import DeletionEvidenceEntity, ProspectStatus
from ...utils.common.logger import Logger
from ...utils.crypto import get_phone_hasher
from ...utils.errors import DatabaseError

logger = Logger(__name__).get_logger()

_EXTERNAL_PREFIXES = ("sms:%", "voice:%")


def _days(raw: Any) -> float | None:
    """None/0/invalid -> disabled (None); positive number -> days."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


@dataclass(frozen=True)
class RetentionPolicies:
    archived_session_days: float | None = None
    sms_message_days: float | None = None
    genie_conversation_days: float | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "RetentionPolicies":
        cfg = dict(raw or {})
        return cls(
            archived_session_days=_days(cfg.get("archived_session_days")),
            sms_message_days=_days(cfg.get("sms_message_days")),
            genie_conversation_days=_days(cfg.get("genie_conversation_days")),
        )


def get_retention_policies() -> RetentionPolicies:
    """The top-level `retention:` yaml section (see module docstring for
    why it is parsed from the file rather than ApplicationContext)."""
    try:
        path = get_application_context().file_path
        with path.open("r", encoding="utf-8-sig") as fh:
            content = yaml.safe_load(fh) or {}
        return RetentionPolicies.from_mapping(content.get("retention") or {})
    except Exception:  # noqa: BLE001 — unreadable config = retention disabled
        logger.error("Retention config could not be read — sweeps disabled", exc_info=True)
        return RetentionPolicies()


class RetentionService:
    def __init__(self, policies: RetentionPolicies | None = None) -> None:
        self._db = get_postgres_connector()
        self._policies = policies or get_retention_policies()
        self._phones = get_phone_hasher()

    # ------------------------------------------------------------------ #
    # Scheduled sweeps                                                    #
    # ------------------------------------------------------------------ #

    async def run_once(self) -> dict[str, int]:
        """One pass over every configured policy. Each sweep is isolated —
        a failing policy logs and the others still run."""
        results: dict[str, int] = {}
        sweeps = (
            ("chat_sessions_purged", self._purge_archived_sessions),
            ("sms_bodies_overwritten", self._overwrite_old_sms_bodies),
            ("genie_conversations_deleted", self._purge_genie_conversations),
            ("external_memory_pruned", self._purge_external_memory),
        )
        for key, sweep in sweeps:
            try:
                results[key] = await sweep()
            except Exception:  # noqa: BLE001 — one policy never stops the pass
                logger.error("Retention sweep failed", extra={"sweep": key}, exc_info=True)
                results[key] = 0
        return results

    async def _purge_archived_sessions(self) -> int:
        days = self._policies.archived_session_days
        if days is None:
            return 0
        cutoff = self._cutoff(days)
        try:
            async with self._db.session() as session:
                rows = (
                    await session.execute(
                        sql_text(
                            """
                            DELETE FROM chat_sessions
                            WHERE archived_at IS NOT NULL AND archived_at < :cutoff
                            RETURNING id, tenant_id
                            """
                        ),
                        {"cutoff": cutoff},
                    )
                ).all()
                if not rows:
                    return 0
                # session_summaries has no FK — reap the orphans explicitly.
                await session.execute(
                    sql_text(
                        "DELETE FROM session_summaries "
                        "WHERE session_id = ANY(CAST(:ids AS text[]))"
                    ),
                    {"ids": [str(r[0]) for r in rows]},
                )
                per_tenant: dict[str, int] = {}
                for _, tenant_id in rows:
                    per_tenant[str(tenant_id)] = per_tenant.get(str(tenant_id), 0) + 1
                for tenant_id, count in per_tenant.items():
                    self._add_evidence(
                        session, tenant_id=tenant_id, action="retention_purge",
                        target="chat_sessions(+cascade)", count=count,
                    )
                return len(rows)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def _overwrite_old_sms_bodies(self) -> int:
        days = self._policies.sms_message_days
        if days is None:
            return 0
        cutoff = self._cutoff(days)
        try:
            async with self._db.session() as session:
                rows = (
                    await session.execute(
                        sql_text(
                            """
                            UPDATE sms_messages
                            SET body = '', vendor_details = '{}'::jsonb
                            WHERE created_at < :cutoff AND body <> ''
                            RETURNING tenant_id
                            """
                        ),
                        {"cutoff": cutoff},
                    )
                ).all()
                if not rows:
                    return 0
                per_tenant: dict[str, int] = {}
                for (tenant_id,) in rows:
                    per_tenant[str(tenant_id)] = per_tenant.get(str(tenant_id), 0) + 1
                for tenant_id, count in per_tenant.items():
                    self._add_evidence(
                        session, tenant_id=tenant_id, action="retention_overwrite",
                        target="sms_messages.body+vendor_details", count=count,
                    )
                return len(rows)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def _purge_genie_conversations(self) -> int:
        days = self._policies.genie_conversation_days
        if days is None:
            return 0
        cutoff = self._cutoff(days)
        try:
            async with self._db.session() as session:
                rows = (
                    await session.execute(
                        sql_text(
                            """
                            DELETE FROM genie_conversations
                            WHERE updated_at < :cutoff
                            RETURNING tenant_id
                            """
                        ),
                        {"cutoff": cutoff},
                    )
                ).all()
                if not rows:
                    return 0
                per_tenant: dict[str, int] = {}
                for (tenant_id,) in rows:
                    per_tenant[str(tenant_id)] = per_tenant.get(str(tenant_id), 0) + 1
                for tenant_id, count in per_tenant.items():
                    self._add_evidence(
                        session, tenant_id=tenant_id, action="retention_purge",
                        target="genie_conversations", count=count,
                    )
                return len(rows)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    async def _purge_external_memory(self) -> int:
        """§8.3 consent-bound countdown: external subjects' facts/episodes
        older than agents.memory.external.retention_days are deleted."""
        # Lazy import — services.memory is a heavier package; retention
        # must stay importable from anywhere without cycles.
        from ..memory.policy import get_external_memory_policy

        days = get_external_memory_policy().retention_days
        if days is None or days <= 0:
            return 0
        cutoff = self._cutoff(days)
        total = 0
        try:
            async with self._db.session() as session:
                for table in ("memory_facts", "memory_episodes"):
                    rows = (
                        await session.execute(
                            sql_text(
                                f"""
                                DELETE FROM {table}
                                WHERE created_at < :cutoff
                                  AND (user_id LIKE :sms OR user_id LIKE :voice)
                                RETURNING tenant_id
                                """
                            ),
                            {
                                "cutoff": cutoff,
                                "sms": _EXTERNAL_PREFIXES[0],
                                "voice": _EXTERNAL_PREFIXES[1],
                            },
                        )
                    ).all()
                    if not rows:
                        continue
                    per_tenant: dict[str, int] = {}
                    for (tenant_id,) in rows:
                        per_tenant[str(tenant_id)] = per_tenant.get(str(tenant_id), 0) + 1
                    for tenant_id, count in per_tenant.items():
                        self._add_evidence(
                            session, tenant_id=tenant_id, action="retention_purge",
                            target=f"{table}(external)", count=count,
                        )
                    total += len(rows)
                return total
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    # ------------------------------------------------------------------ #
    # Right to erasure (§8.3)                                             #
    # ------------------------------------------------------------------ #

    async def erase_external_subject(self, *, tenant_id: str, phone: str) -> dict[str, int]:
        """Erase one campaign recipient. Evidence rows use the phone's HMAC
        token as the target — never the raw number."""
        cleaned = (phone or "").strip()
        user_id = f"sms:{cleaned}"
        phone_hash = self._phones.hash(cleaned)
        token = f"subject:{phone_hash[:16]}"
        summary: dict[str, int] = {}
        try:
            async with self._db.session() as session:
                # 1. Memory facts + episodes for the subject.
                for table in ("memory_facts", "memory_episodes"):
                    result = await session.execute(
                        sql_text(
                            f"DELETE FROM {table} WHERE tenant_id = :tenant AND user_id = :user"
                        ),
                        {"tenant": tenant_id, "user": user_id},
                    )
                    summary[table] = int(result.rowcount or 0)

                # 2. Prospects: cancelled AND content blanked (row retained
                # as evidence of the commitment having existed).
                result = await session.execute(
                    sql_text(
                        """
                        UPDATE memory_prospects
                        SET content = '',
                            status = CASE WHEN status = :open THEN :cancelled ELSE status END
                        WHERE tenant_id = :tenant AND user_id = :user
                        """
                    ),
                    {
                        "tenant": tenant_id, "user": user_id,
                        "open": ProspectStatus.OPEN,
                        "cancelled": ProspectStatus.CANCELLED,
                    },
                )
                summary["memory_prospects"] = int(result.rowcount or 0)

                # 3. SMS thread sessions -> hard DELETE (cascade reaps
                # messages, edits, session_documents, sms_threads).
                session_rows = (
                    await session.execute(
                        sql_text(
                            """
                            SELECT session_id FROM sms_threads
                            WHERE tenant_id = :tenant
                              AND (phone_hash = :hash OR phone = :phone)
                            """
                        ),
                        {"tenant": tenant_id, "hash": phone_hash, "phone": cleaned},
                    )
                ).all()
                session_ids = [str(r[0]) for r in session_rows]
                if session_ids:
                    result = await session.execute(
                        sql_text(
                            "DELETE FROM chat_sessions "
                            "WHERE id = ANY(CAST(:ids AS text[]))"
                        ),
                        {"ids": session_ids},
                    )
                    summary["chat_sessions"] = int(result.rowcount or 0)
                    await session.execute(
                        sql_text(
                            "DELETE FROM session_summaries "
                            "WHERE session_id = ANY(CAST(:ids AS text[]))"
                        ),
                        {"ids": session_ids},
                    )
                else:
                    summary["chat_sessions"] = 0

                # 4. Message bodies overwritten; delivery ledger retained.
                result = await session.execute(
                    sql_text(
                        """
                        UPDATE sms_messages
                        SET body = '', vendor_details = '{}'::jsonb
                        WHERE tenant_id = :tenant
                          AND (phone_hash = :hash OR phone = :phone)
                          AND body <> ''
                        """
                    ),
                    {"tenant": tenant_id, "hash": phone_hash, "phone": cleaned},
                )
                summary["sms_messages"] = int(result.rowcount or 0)

                for target, count in summary.items():
                    if count:
                        self._add_evidence(
                            session, tenant_id=tenant_id, action="erasure",
                            target=f"{target}:{token}", count=count,
                        )
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        logger.info(
            "External subject erased",
            extra={"subject": token, "counts": summary},
        )
        return summary

    async def cancel_prospects_for_subject(self, *, tenant_id: str, user_id: str) -> int:
        """Consent hook (§8.3): opt-out cancels the subject's OPEN
        prospects. Content stays encrypted in place until erasure or the
        external retention countdown removes it."""
        try:
            async with self._db.session() as session:
                result = await session.execute(
                    sql_text(
                        """
                        UPDATE memory_prospects
                        SET status = :cancelled
                        WHERE tenant_id = :tenant AND user_id = :user AND status = :open
                        """
                    ),
                    {
                        "tenant": tenant_id, "user": user_id,
                        "open": ProspectStatus.OPEN,
                        "cancelled": ProspectStatus.CANCELLED,
                    },
                )
                count = int(result.rowcount or 0)
                if count:
                    token = self._phones.hash(user_id)[:16]
                    self._add_evidence(
                        session, tenant_id=tenant_id, action="prospects_cancelled",
                        target=f"memory_prospects:subject:{token}", count=count,
                    )
                return count
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _add_evidence(session, *, tenant_id: str, action: str, target: str, count: int) -> None:
        session.add(
            DeletionEvidenceEntity(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                action=action,
                target=target,
                count=count,
                executed_at=datetime.now(timezone.utc),
            )
        )

    @staticmethod
    def _cutoff(days: float) -> datetime:
        return datetime.now(timezone.utc) - timedelta(days=days)


_service: RetentionService | None = None


def get_retention_service() -> RetentionService:
    global _service
    if _service is None:
        _service = RetentionService()
    return _service
