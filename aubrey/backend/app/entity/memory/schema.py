"""ANN indexes for the memory tables — same choices as entity/knowledge:
HNSW over a halfvec cast (pgvector caps full-precision HNSW at 2000 dims;
the columns are vector(3072)), m=16 / ef_construction=128, partial on
non-null embeddings. Recall filters on (tenant, user) first, so these stay
small per subject; the index keeps cold-start recall flat as stores grow."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from backend.app.entity.knowledge.vector_type import DEFAULT_EMBEDDING_DIMENSIONS

MEMORY_PGVECTOR_INDEX_SQL: tuple[str, ...] = (
    f"""
    CREATE INDEX IF NOT EXISTS idx_memory_facts_embedding_hnsw
    ON memory_facts
    USING hnsw ((embedding::halfvec({DEFAULT_EMBEDDING_DIMENSIONS})) halfvec_cosine_ops)
    WITH (m=16, ef_construction=128)
    WHERE embedding IS NOT NULL
    """,
    f"""
    CREATE INDEX IF NOT EXISTS idx_memory_episodes_embedding_hnsw
    ON memory_episodes
    USING hnsw ((embedding::halfvec({DEFAULT_EMBEDDING_DIMENSIONS})) halfvec_cosine_ops)
    WITH (m=16, ef_construction=128)
    WHERE embedding IS NOT NULL
    """,
)


async def ensure_memory_indexes(conn: AsyncConnection) -> None:
    for sql in MEMORY_PGVECTOR_INDEX_SQL:
        await conn.execute(text(sql))
