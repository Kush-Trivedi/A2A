import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.agents import OdtTeamEntity, TeamTokenEntity
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError, NotFoundError

logger = Logger(__name__).get_logger()


class TeamTokenService:
    """Admin-issued registration tokens, one or more per team.

    The raw token is returned exactly once; only its SHA-256 hash is stored.
    Agents present `Authorization: Bearer <token>` to self-register — the
    token pins them to their team, so a team can never register an agent
    under another team's key.
    """

    _TOKEN_PREFIX = "ace-tk-"

    def __init__(self) -> None:
        self._connector = get_postgres_connector()

    @staticmethod
    def _hash(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def issue(
        self, *, context: SessionContext, team_key: str, label: str = ""
    ) -> str:
        normalized = (team_key or "").strip().lower()
        raw_token = self._TOKEN_PREFIX + secrets.token_urlsafe(32)
        try:
            async with self._connector.session() as session:
                team = (
                    await session.exec(
                        select(OdtTeamEntity).where(
                            OdtTeamEntity.tenant_id == context.tenant_id,
                            OdtTeamEntity.key == normalized,
                        )
                    )
                ).first()
                if team is None:
                    raise NotFoundError(
                        "Team is not registered.", details={"team_key": normalized}
                    )
                session.add(
                    TeamTokenEntity(
                        id=uuid.uuid4().hex,
                        tenant_id=context.tenant_id,
                        team_key=normalized,
                        token_hash=self._hash(raw_token),
                        label=label,
                    )
                )
        except NotFoundError:
            raise
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc
        logger.info(
            "Team registration token issued",
            extra={"team_key": normalized, "label": label},
        )
        return raw_token

    async def validate(self, raw_token: str) -> TeamTokenEntity | None:
        candidate = (raw_token or "").strip()
        if not candidate.startswith(self._TOKEN_PREFIX):
            return None
        try:
            async with self._connector.session() as session:
                token = (
                    await session.exec(
                        select(TeamTokenEntity).where(
                            TeamTokenEntity.token_hash == self._hash(candidate),
                            TeamTokenEntity.revoked == False,  # noqa: E712
                        )
                    )
                ).first()
                if token is not None:
                    token.last_used_at = datetime.now(timezone.utc)
                    session.add(token)
                return token
        except Exception:  # noqa: BLE001 — validation failure = unauthenticated, not 500
            logger.error("Team token validation failed", exc_info=True)
            return None

    async def list_for_team(
        self, *, context: SessionContext, team_key: str
    ) -> list[dict]:
        """Masked inventory — hash prefix only, never a usable value."""
        from sqlmodel import select

        normalized = team_key.strip().lower()
        async with self._db.session() as session:
            rows = (
                await session.exec(
                    select(TeamTokenEntity)
                    .where(
                        TeamTokenEntity.tenant_id == context.tenant_id,
                        TeamTokenEntity.team_key == normalized,
                    )
                    .order_by(TeamTokenEntity.created_at.desc())  # type: ignore[attr-defined]
                )
            ).all()
        return [
            {
                "id": t.id,
                "label": t.label,
                "masked": f"aub_****{t.token_hash[:6]}",
                "revoked": t.revoked,
                "created_at": t.created_at.isoformat(),
                "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
            }
            for t in rows
        ]

    async def revoke(self, *, context: SessionContext, team_key: str) -> int:
        """Revoke every token for a team (rotation = revoke + issue)."""
        try:
            async with self._connector.session() as session:
                rows = (
                    await session.exec(
                        select(TeamTokenEntity).where(
                            TeamTokenEntity.tenant_id == context.tenant_id,
                            TeamTokenEntity.team_key == team_key.strip().lower(),
                            TeamTokenEntity.revoked == False,  # noqa: E712
                        )
                    )
                ).all()
                for token in rows:
                    token.revoked = True
                    session.add(token)
                return len(rows)
        except Exception as exc:
            raise DatabaseError(cause=exc) from exc


_service: TeamTokenService | None = None


def get_team_token_service() -> TeamTokenService:
    global _service
    if _service is None:
        _service = TeamTokenService()
    return _service
