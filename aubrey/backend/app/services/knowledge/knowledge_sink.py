"""The pipeline's DocumentSink: converted text in, searchable knowledge out.

    chunk (user-chosen strategy, adaptive sizes) -> embed dense vectors
    (identical text reuses an existing vector instead of calling the API)
    -> store chunks (sparse tsvector generates itself) -> document becomes
    'embedded' -> optionally extract graph entities per chunk

Re-running for the same document is safe: existing chunks are replaced,
not duplicated. Chunking/embedding failures raise — the pipeline counts
the file failed and the document stays 'converted' for the self-heal
re-embed on the next run. Graph extraction failures only log (additive)."""

import hashlib
import uuid

from sqlalchemy import delete
from sqlmodel import select

from ...database.rdbms.pg_session import get_postgres_connector
from ...entity.documents import DocumentEntity, DocumentStatus
from ...entity.knowledge import DocumentChunkEntity
from ...services.documents.document_pipeline import DocumentSink
from ...utils.common.logger import Logger
from .chunkers import build_chunker
from .embedding_service import EmbeddingService, get_embedding_service
from .graph_service import GraphExtractionService, get_graph_extraction_service

logger = Logger(__name__).get_logger()


class KnowledgeSinkFactory:
    def __init__(
        self,
        embeddings: EmbeddingService | None = None,
        graph: GraphExtractionService | None = None,
    ) -> None:
        self._embeddings = embeddings or get_embedding_service()
        self._graph = graph or get_graph_extraction_service()
        self._db = get_postgres_connector()

    def make_sink(
        self, *, tenant_id: str, chunking_strategy: str, build_graph: bool
    ) -> DocumentSink:
        chunker = build_chunker(chunking_strategy, embed_fn=self._embeddings.embed)

        async def sink(document_id: str, file_name: str, text: str) -> None:
            chunks = await chunker.split(text)
            if not chunks:
                logger.warning(
                    "Document produced no chunks; leaving status 'converted'",
                    extra={"document_id": document_id, "file_name": file_name},
                )
                return

            embedding_texts = [c.embedding_text or c.content for c in chunks]
            hashes = [
                hashlib.sha256(t.encode("utf-8")).hexdigest() for t in embedding_texts
            ]
            vectors = await self._embed_with_reuse(tenant_id, embedding_texts, hashes)

            chunk_ids = await self._store_chunks(
                tenant_id=tenant_id,
                document_id=document_id,
                chunks=chunks,
                hashes=hashes,
                vectors=vectors,
                strategy=chunker.name,
            )
            logger.info(
                "Document embedded",
                extra={
                    "document_id": document_id,
                    "file_name": file_name,
                    "strategy": chunker.name,
                    "chunks": len(chunk_ids),
                },
            )

            if build_graph:
                for chunk_id, chunk in zip(chunk_ids, chunks):
                    await self._graph.extract_chunk(
                        tenant_id=tenant_id, chunk_id=chunk_id, text=chunk.content
                    )

        return sink

    async def _embed_with_reuse(
        self, tenant_id: str, texts: list[str], hashes: list[str]
    ) -> list[list[float]]:
        """Identical text (same hash + model) reuses its stored vector; only
        genuinely new text hits the embedding API."""
        model = self._embeddings.model_name
        reused: dict[str, list[float]] = {}
        async with self._db.session() as session:
            for text_hash in set(hashes):
                row = (
                    await session.exec(
                        select(DocumentChunkEntity).where(
                            DocumentChunkEntity.tenant_id == tenant_id,
                            DocumentChunkEntity.text_sha256 == text_hash,
                            DocumentChunkEntity.embedding_model == model,
                            DocumentChunkEntity.embedding.is_not(None),  # type: ignore[union-attr]
                        )
                    )
                ).first()
                if row is not None:
                    reused[text_hash] = row.embedding

        new_indexes = [i for i, h in enumerate(hashes) if h not in reused]
        new_vectors = await self._embeddings.embed([texts[i] for i in new_indexes])
        for index, vector in zip(new_indexes, new_vectors):
            reused[hashes[index]] = vector
        if len(new_indexes) < len(hashes):
            logger.info(
                "Embedding reuse",
                extra={
                    "total": len(hashes),
                    "embedded": len(new_indexes),
                    "reused": len(hashes) - len(new_indexes),
                },
            )
        return [reused[h] for h in hashes]

    async def _store_chunks(
        self,
        *,
        tenant_id: str,
        document_id: str,
        chunks: list,
        hashes: list[str],
        vectors: list[list[float]],
        strategy: str,
    ) -> list[str]:
        model = self._embeddings.model_name
        chunk_ids: list[str] = []
        async with self._db.session() as session:
            # replace, never duplicate — safe for self-heal re-runs
            await session.execute(
                delete(DocumentChunkEntity).where(
                    DocumentChunkEntity.document_id == document_id
                )
            )
            for chunk, text_hash, vector in zip(chunks, hashes, vectors):
                chunk_id = uuid.uuid4().hex
                chunk_ids.append(chunk_id)
                session.add(
                    DocumentChunkEntity(
                        id=chunk_id,
                        tenant_id=tenant_id,
                        document_id=document_id,
                        chunk_index=chunk.index,
                        content=chunk.content,
                        embedding_text=chunk.embedding_text or chunk.content,
                        token_count=chunk.token_count,
                        text_sha256=text_hash,
                        embedding=vector,
                        embedding_model=model,
                        chunk_metadata={**chunk.metadata, "strategy": strategy},
                    )
                )
            document = (
                await session.exec(
                    select(DocumentEntity).where(DocumentEntity.id == document_id)
                )
            ).one()
            document.status = DocumentStatus.EMBEDDED
            document.chunk_count = len(chunk_ids)
            session.add(document)
        return chunk_ids


_factory: KnowledgeSinkFactory | None = None


def get_knowledge_sink_factory() -> KnowledgeSinkFactory:
    global _factory
    if _factory is None:
        _factory = KnowledgeSinkFactory()
    return _factory
