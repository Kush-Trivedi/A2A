from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from backend.app.entity.pgvector.vector_type import DEFAULT_EMBEDDING_DIMENSIONS

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

async def ensure_pgvector_indexes(conn: AsyncConnection) -> None:
    for sql in PGVECTOR_INDEX_SQL:
        await conn.execute(text(sql))

async def ensure_pgvector_schema(conn: AsyncConnection) -> None:
    await ensure_pgvector_indexes(conn)
