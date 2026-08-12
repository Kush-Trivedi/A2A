"""pgvector bootstrap: the extension must exist before create_all makes the
vector columns, and the ANN indexes are created after the tables exist.

Index choices (dense + sparse):
- HNSW over IVFFlat: no training step, works on an empty table, better
  recall at the same latency. m=16 / ef_construction=128 are the accepted
  production defaults.
- The embedding columns are vector(3072); pgvector caps HNSW at 2000 dims
  for full-precision vectors, so the index is built over a halfvec cast —
  half-precision is standard practice and costs ~no recall at 3072 dims.
- Sparse search uses the GENERATED tsvector column with its GIN index
  (declared on the entity), plus GIN on chunk metadata for filtered
  retrieval. Nothing here needs a nightly rebuild; all indexes maintain
  themselves on write."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.entity.knowledge.vector_type import DEFAULT_EMBEDDING_DIMENSIONS

PGVECTOR_INDEX_SQL: tuple[str, ...] = (
    f"""
    CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw
    ON document_chunks
    USING hnsw ((embedding::halfvec({DEFAULT_EMBEDDING_DIMENSIONS})) halfvec_cosine_ops)
    WITH (m=16, ef_construction=128)
    WHERE embedding IS NOT NULL
    """,
    f"""
    CREATE INDEX IF NOT EXISTS idx_knowledge_graph_nodes_embedding_hnsw
    ON knowledge_graph_nodes
    USING hnsw ((embedding::halfvec({DEFAULT_EMBEDDING_DIMENSIONS})) halfvec_cosine_ops)
    WITH (m=16, ef_construction=128)
    WHERE embedding IS NOT NULL
    """,
)


async def ensure_pgvector_extension(conn: AsyncConnection) -> None:
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


async def ensure_pgvector_indexes(conn: AsyncConnection) -> None:
    for sql in PGVECTOR_INDEX_SQL:
        await conn.execute(text(sql))
