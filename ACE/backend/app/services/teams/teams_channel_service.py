import base64
import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from sqlmodel import select

from ...config.application_context import get_application_context
from ...config.settings_validator import PlaceholderPolicy
from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.teams import TeamsConversationEntity, TeamsMessageEntity
from ...security.field_encryptor import FieldEncryptor, get_field_encryptor
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import DatabaseError
from ..agents.registry_service import AgentRegistryService, get_agent_registry_service
from ..conversation.conversation_service import ConversationService, get_conversation_service
from ...entity.authz import UserRoleAssignmentEntity

logger = Logger(__name__).get_logger()

_MENTION_RE = re.compile(r"<at>.*?</at>\s*", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class TeamsSettings:
    """Channel defaults only — webhook secrets are per-agent (team_config),
    never platform-wide."""

    default_agent: str
    tenant_id: str
    default_roles: tuple[str, ...]


@lru_cache(maxsize=1)
def get_teams_settings() -> TeamsSettings:
    cfg = get_application_context().microsoft.get("microsoft_teams", {}) or {}
    agent = str(cfg.get("agent") or "")
    return TeamsSettings(
        default_agent=agent if PlaceholderPolicy.is_configured(agent) else "general",
        tenant_id=str(cfg.get("tenant_id") or "default"),
        default_roles=tuple(cfg.get("default_roles") or ["teams_user"]),
    )


@dataclass(frozen=True)
class TeamsReply:
    text: str
    conversation_id: str


@dataclass(frozen=True)
class TeamsBinding:
    """Whether an agent is exposed on Microsoft Teams, and with which secret."""

    enabled: bool
    secret: str


class TeamsChannelService:
    """Teams outgoing-webhook bridge — same shape as the SMS channel.

    Teams POSTs an Activity when the webhook is @mentioned; we validate the
    HMAC (per-agent team secret, or the platform yaml secret on the default
    route), map (conversation, user) to an ACE chat session,
    route through the normal pipeline to the conversation's agent, and reply
    INLINE in the HTTP response (Teams requires the answer in ~5 seconds).
    """

    def __init__(
        self,
        settings: TeamsSettings | None = None,
        conversations: ConversationService | None = None,
        encryptor: FieldEncryptor | None = None,
        registry_service: AgentRegistryService | None = None,
    ) -> None:
        self._settings = settings or get_teams_settings()
        self._chat = conversations or get_conversation_service()
        self._crypto = encryptor or get_field_encryptor()
        self._registry = registry_service or get_agent_registry_service()
        self._connector = get_postgres_connector()

    async def binding_for(self, agent_key: str | None) -> TeamsBinding:
        """Teams is OPT-IN per agent: the owning team declares
        `channels.teams.enabled` (+ their webhook secret) in their env config,
        which flows to registry team_config at registration. Agents whose team
        never opted in are not reachable over the Teams channel — there is no
        platform-wide route or secret anymore."""
        if agent_key is None:
            return TeamsBinding(enabled=False, secret="")
        pair = await self._registry.get_agent_with_team(
            tenant_id=self._settings.tenant_id, agent_key=agent_key
        )
        if pair is None:
            return TeamsBinding(enabled=False, secret="")
        channel = ((pair[0].team_config or {}).get("channels") or {}).get("teams") or {}
        if not channel.get("enabled"):
            return TeamsBinding(enabled=False, secret="")
        return TeamsBinding(enabled=True, secret=str(channel.get("webhook_secret") or ""))

    def validate_signature(
        self, *, raw_body: bytes, authorization: str, secret: str
    ) -> bool:
        """HMAC-SHA256 over the raw body with the AGENT's webhook secret
        (resolved by binding_for) — there is no platform-wide secret."""
        if not PlaceholderPolicy.is_configured(secret):
            logger.warning("Teams webhook secret not configured — HMAC check skipped (dev only).")
            return True
        scheme, _, provided = authorization.partition(" ")
        if scheme.strip().upper() != "HMAC" or not provided.strip():
            return False
        try:
            key = base64.b64decode(secret)
        except Exception:  # noqa: BLE001
            key = secret.encode()
        digest = hmac.new(key, raw_body, hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode()
        return hmac.compare_digest(expected, provided.strip())

    async def _roles_for(self, aad_object_id: str) -> tuple[str, ...]:
        """Real user roles: Teams users are signed in with their Microsoft id,
        so look up the roles Entra login provisioned for that person."""
        if not aad_object_id:
            return self._settings.default_roles
        try:
            async with self._connector.session() as session:
                rows = (
                    await session.exec(
                        select(UserRoleAssignmentEntity.role).where(
                            UserRoleAssignmentEntity.tenant_id == self._settings.tenant_id,
                            UserRoleAssignmentEntity.user_id == aad_object_id,
                        )
                    )
                ).all()
            if rows:
                return tuple(dict.fromkeys([*rows, *self._settings.default_roles]))
        except Exception:  # noqa: BLE001
            logger.warning("Teams role lookup failed; using default roles", exc_info=True)
        return self._settings.default_roles

    @staticmethod
    def _clean_text(activity: dict[str, Any]) -> str:
        return _MENTION_RE.sub("", str(activity.get("text") or "")).strip()

    def _conversation_hash(self, conversation_id: str, user_id: str) -> str:
        pepper = str(
            get_application_context().security.get("identity_hash_pepper") or "ace-teams"
        )
        return hmac.new(
            pepper.encode(),
            f"{self._settings.tenant_id}|teams|{conversation_id}|{user_id}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def _teams_context(
        self,
        conversation: TeamsConversationEntity,
        aad_object_id: str,
        roles: tuple[str, ...],
    ) -> SessionContext:
        now = datetime.now(timezone.utc)
        actor = aad_object_id or f"teams:{conversation.conversation_hash[:12]}"
        return SessionContext(
            session_id=f"teams-{conversation.id}",
            tenant_id=conversation.tenant_id,
            user_id=actor,
            actor_id=actor,
            email="",
            display_name=conversation.user_display_name or "Teams User",
            auth_provider="teams",
            csrf_token="",
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(minutes=10),
            roles=roles,
        )

    async def handle_activity(
        self, activity: dict[str, Any], *, agent_key: str | None = None
    ) -> TeamsReply:
        text = self._clean_text(activity)
        from_block = activity.get("from") or {}
        user_id = str(from_block.get("id") or "unknown")
        aad_object_id = str(from_block.get("aadObjectId") or "")
        display_name = str(from_block.get("name") or "")
        teams_conversation_id = str((activity.get("conversation") or {}).get("id") or "unknown")
        activity_id = str(activity.get("id") or "")

        conversation = await self._find_or_create(
            teams_conversation_id=teams_conversation_id,
            user_id=user_id,
            display_name=display_name,
            agent_key=agent_key,
        )
        await self._store_message(conversation, "inbound", text, activity_id)

        if not text:
            return TeamsReply("Ask me a question after the mention.", conversation.id)

        roles = await self._roles_for(aad_object_id)
        context = self._teams_context(conversation, aad_object_id, roles)
        result = await self._chat.send(
            context=context,
            agent_id=conversation.agent_key,
            message=text,
            session_id=conversation.chat_session_id,
        )
        if conversation.chat_session_id != result.session_id:
            await self._bind_session(conversation.id, result.session_id)
        await self._store_message(conversation, "outbound", result.answer, None)
        return TeamsReply(result.answer, conversation.id)

    async def _find_or_create(
        self,
        *,
        teams_conversation_id: str,
        user_id: str,
        display_name: str,
        agent_key: str | None = None,
    ) -> TeamsConversationEntity:
        conversation_hash = self._conversation_hash(teams_conversation_id, user_id)
        try:
            async with self._connector.session() as session:
                existing = (
                    await session.exec(
                        select(TeamsConversationEntity).where(
                            TeamsConversationEntity.tenant_id == self._settings.tenant_id,
                            TeamsConversationEntity.conversation_hash == conversation_hash,
                        )
                    )
                ).first()
                if existing is not None:
                    if agent_key and existing.agent_key != agent_key:
                        existing.agent_key = agent_key
                        session.add(existing)
                    return existing
                now = datetime.now(timezone.utc)
                conversation = TeamsConversationEntity(
                    id=uuid.uuid4().hex,
                    tenant_id=self._settings.tenant_id,
                    conversation_hash=conversation_hash,
                    teams_user_id=self._crypto.encrypt(user_id) or user_id,
                    user_display_name=display_name,
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
        conversation: TeamsConversationEntity,
        direction: str,
        body: str,
        activity_id: str | None,
    ) -> None:
        now = datetime.now(timezone.utc)
        entity = TeamsMessageEntity(
            id=uuid.uuid4().hex,
            tenant_id=conversation.tenant_id,
            conversation_id=conversation.id,
            direction=direction,
            message_body=self._crypto.encrypt(body) or body,
            activity_id=activity_id or None,
            created_at=now,
            updated_at=now,
        )
        try:
            async with self._connector.session() as session:
                session.add(entity)
        except Exception:  # noqa: BLE001 — duplicate activity id (retry) is fine
            logger.info("Teams message store skipped (likely duplicate activity id)")

    async def _bind_session(self, conversation_id: str, chat_session_id: str) -> None:
        async with self._connector.session() as session:
            conversation = (
                await session.exec(
                    select(TeamsConversationEntity).where(
                        TeamsConversationEntity.id == conversation_id
                    )
                )
            ).one()
            conversation.chat_session_id = chat_session_id
            conversation.updated_at = datetime.now(timezone.utc)
            session.add(conversation)


_service: TeamsChannelService | None = None


def get_teams_channel_service() -> TeamsChannelService:
    global _service
    if _service is None:
        _service = TeamsChannelService()
    return _service
