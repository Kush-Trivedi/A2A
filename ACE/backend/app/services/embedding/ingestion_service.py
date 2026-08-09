import hashlib
from dataclasses import dataclass
from ...security.session import SessionContext
from ...utils.common.logger import Logger
from ...utils.document import MarkItDownClient, get_markitdown_client
from ...utils.errors import DocumentProcessingError
from ..knowledge.chunker import ChunkingStrategy, build_chunker
from ..knowledge.gateway import KnowledgeGateway, get_knowledge_gateway
from ..knowledge.provider import IngestChunk, IngestDocument
from ..knowledge.settings import KnowledgeSettings, get_knowledge_settings
from .embedding_service import EmbeddingService, get_embedding_service

logger = Logger(__name__).get_logger()

_DEFAULT_SOURCE_TYPE = "upload"

@dataclass(frozen=True)
class IngestionResult:
    document_id: str
    knowledge_source: str
    source_name: str
    chunk_count: int
    status: str = "processed"


class IngestionService:
    def __init__(
        self,
        markitdown: MarkItDownClient | None = None,
        embedding: EmbeddingService | None = None,
        gateway: KnowledgeGateway | None = None,
        settings: KnowledgeSettings | None = None,
    ) -> None:
        self._markitdown = markitdown or get_markitdown_client()
        self._embedding = embedding or get_embedding_service()
        self._gateway = gateway or get_knowledge_gateway()
        self._settings = settings or get_knowledge_settings()

    def _build_chunker(self, strategy: str | None) -> ChunkingStrategy:
        return build_chunker(
            strategy or self._settings.chunking_strategy,
            max_chunk_size=self._settings.chunk_size,
            chunk_overlap=self._settings.chunk_overlap,
            min_chunk_size=self._settings.chunk_min_size,
            target_chunks=self._settings.chunk_target_count,
            embed_fn=self._embedding.embed_texts,
            breakpoint_percentile=self._settings.semantic_breakpoint_percentile,
        )

    async def ingest_file(
        self,
        *,
        context: SessionContext,
        knowledge_source: str,
        filename: str,
        raw_bytes: bytes,
        session_id: str | None = None,
        source_type: str = _DEFAULT_SOURCE_TYPE,
        dimensions: int | None = None,
        chunking_strategy: str | None = None,
    ) -> IngestionResult:
        text = await self._markitdown.aconvert_bytes(raw_bytes, filename)
        return await self._ingest_text(
            context=context,
            knowledge_source=knowledge_source,
            source_name=filename,
            text=text,
            raw_size=len(raw_bytes),
            session_id=session_id,
            source_type=source_type,
            dimensions=dimensions,
            chunking_strategy=chunking_strategy,
        )

    async def ingest_text(
        self,
        *,
        context: SessionContext,
        knowledge_source: str,
        title: str,
        text: str,
        session_id: str | None = None,
        source_type: str = _DEFAULT_SOURCE_TYPE,
        dimensions: int | None = None,
        chunking_strategy: str | None = None,
    ) -> IngestionResult:
        return await self._ingest_text(
            context=context,
            knowledge_source=knowledge_source,
            source_name=title,
            text=text,
            raw_size=len(text.encode("utf-8")),
            session_id=session_id,
            source_type=source_type,
            dimensions=dimensions,
            chunking_strategy=chunking_strategy,
        )

    def _is_owner_upload(self, knowledge_source: str, session_id: str | None) -> bool:
        return session_id is not None and knowledge_source == self._settings.upload_source

    async def _ingest_text(
        self,
        *,
        context: SessionContext,
        knowledge_source: str,
        source_name: str,
        text: str,
        raw_size: int,
        session_id: str | None,
        source_type: str,
        dimensions: int | None,
        chunking_strategy: str | None = None,
    ) -> IngestionResult:
        if not self._is_owner_upload(knowledge_source, session_id):
            await self._gateway.ensure_can_write(context, knowledge_source)

        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = await self._gateway.find_duplicate(
            tenant_id=context.tenant_id,
            sha256=content_hash,
            knowledge_source=knowledge_source,
            session_id=session_id,
        )
        if existing is not None:
            document_id, chunk_count = existing
            logger.info(
                "Duplicate document skipped (content hash match)",
                extra={
                    "document_id": document_id,
                    "knowledge_source": knowledge_source,
                    "source_name": source_name,
                },
            )
            return IngestionResult(
                document_id=document_id,
                knowledge_source=knowledge_source,
                source_name=source_name,
                chunk_count=chunk_count,
                status="skipped",
            )

        chunker = self._build_chunker(chunking_strategy)
        chunks = await chunker.split(text)
        if not chunks:
            raise DocumentProcessingError(
                "The document produced no indexable text.",
                details={"source_name": source_name},
            )

        vectors = await self._embedding.embed_texts(
            [chunk.embedding_text or chunk.content for chunk in chunks],
            dimensions=dimensions,
        )
        if len(vectors) != len(chunks):
            raise DocumentProcessingError(
                "Embedding count did not match chunk count.",
                details={"chunks": len(chunks), "vectors": len(vectors)},
            )

        resolved_dims = self._embedding.dimensions(dimensions)
        ingest_chunks = tuple(
            IngestChunk(
                chunk_index=chunk.index,
                content=chunk.content,
                embedding=vector,
                token_count=chunk.token_count,
                embedding_text=chunk.embedding_text,
                metadata={
                    **chunk.metadata,
                    "chunking_strategy": chunker.name,
                },
            )
            for chunk, vector in zip(chunks, vectors)
        )

        document = IngestDocument(
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            knowledge_source=knowledge_source,
            source_name=source_name,
            source_type=source_type,
            sha256=content_hash,
            size_bytes=raw_size,
            embedding_model=self._embedding.model,
            embedding_dimensions=resolved_dims,
            chunks=ingest_chunks,
            session_id=session_id,
            metadata={"ingested_by": context.user_id},
        )
        document_id = await self._gateway.ingest(document)
        return IngestionResult(
            document_id=document_id,
            knowledge_source=knowledge_source,
            source_name=source_name,
            chunk_count=len(ingest_chunks),
        )


_service: IngestionService | None = None


def get_ingestion_service() -> IngestionService:
    global _service
    if _service is None:
        _service = IngestionService()
    return _service
