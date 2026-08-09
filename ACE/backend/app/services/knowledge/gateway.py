from ...security.authorization.enforcer import CasbinEnforcer, get_casbin_enforcer
from ...security.authorization.context_attrs import AuthorizationContextBuilder
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.errors import PermissionDeniedError
from .provider import (
    IngestDocument,
    KnowledgeChunk,
    KnowledgeProvider,
    PgVectorKnowledgeProvider,
    RetrievalQuery,
)
from .settings import KnowledgeSettings, get_knowledge_settings

logger = Logger(__name__).get_logger()


class KnowledgeGateway:
    def __init__(
        self,
        provider: KnowledgeProvider | None = None,
        enforcer: CasbinEnforcer | None = None,
        settings: KnowledgeSettings | None = None,
    ) -> None:
        self._provider = provider or PgVectorKnowledgeProvider()
        self._enforcer = enforcer or get_casbin_enforcer()
        self._settings = settings or get_knowledge_settings()

    def _resource(self, source: str) -> str:
        return f"{self._settings.resource_prefix}{source}"

    async def _can(self, context: SessionContext, source: str, action: str) -> bool:
        if not self._enforcer.enabled:
            return True
        if not context.roles:
            return False
        return await self._enforcer.enforce_any_role(
            context.roles,
            context.tenant_id,
            self._resource(source),
            action,
            AuthorizationContextBuilder.build(context),
        )

    async def authorized_read_sources(
        self, context: SessionContext, requested: tuple[str, ...]
    ) -> tuple[str, ...]:
        allowed: list[str] = []
        for source in requested:
            if await self._can(context, source, self._settings.read_action):
                allowed.append(source)
            else:
                logger.info(
                    "Knowledge source excluded (no read permission)",
                    extra={
                        "tenant_id": context.tenant_id,
                        "user_id": context.user_id,
                        "knowledge_source": source,
                    },
                )
        return tuple(allowed)

    async def ensure_can_write(self, context: SessionContext, source: str) -> None:
        if not await self._can(context, source, self._settings.write_action):
            raise PermissionDeniedError(
                "You are not permitted to ingest into this knowledge source.",
                details={"knowledge_source": source},
            )

    async def retrieve(
        self,
        *,
        context: SessionContext,
        embedding: list[float],
        requested_sources: tuple[str, ...],
        session_id: str | None = None,
        top_k: int | None = None,
        query_text: str = "",
        mode: str = "",
    ) -> list[KnowledgeChunk]:
        allowed = await self.authorized_read_sources(context, requested_sources)
        if not allowed and not session_id:
            return []
        query = RetrievalQuery(
            tenant_id=context.tenant_id,
            embedding=embedding,
            top_k=top_k or self._settings.retrieval_top_k,
            knowledge_sources=allowed,
            min_similarity=self._settings.min_similarity,
            session_id=session_id,
            query_text=query_text,
            candidate_pool=self._settings.retrieval_candidates,
            neighbor_window=self._settings.neighbor_window,
            neighbor_max_window=self._settings.neighbor_max_window,
            neighbor_score_floor=self._settings.neighbor_score_floor,
            context_token_budget=self._settings.context_token_budget,
            mode=mode,
        )
        return await self._provider.retrieve(query)

    async def ingest(self, document: IngestDocument) -> str:
        return await self._provider.upsert(document)

    async def find_duplicate(
        self,
        *,
        tenant_id: str,
        sha256: str,
        knowledge_source: str,
        session_id: str | None = None,
    ) -> tuple[str, int] | None:
        return await self._provider.find_document_by_hash(
            tenant_id=tenant_id,
            sha256=sha256,
            knowledge_source=knowledge_source,
            session_id=session_id,
        )

    async def soft_delete_session_uploads(
        self, *, tenant_id: str, session_id: str
    ) -> int:
        return await self._provider.soft_delete_session_uploads(
            tenant_id=tenant_id, session_id=session_id
        )


_gateway: KnowledgeGateway | None = None


def get_knowledge_gateway() -> KnowledgeGateway:
    global _gateway
    if _gateway is None:
        _gateway = KnowledgeGateway()
    return _gateway
