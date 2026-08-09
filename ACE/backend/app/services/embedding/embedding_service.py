import asyncio
from ...llm.azure_foundry.ace_azure_foundry import AceAzureFoundry
from ...utils.common.logger import Logger
from ...utils.errors import EmbeddingError, LLMRateLimitError
from ..knowledge.settings import KnowledgeSettings, get_knowledge_settings

logger = Logger(__name__).get_logger()

_MAX_RETRIES = 4
_BASE_DELAY_SECONDS = 2.0


def _is_rate_limit(error: Exception) -> bool:
    text = str(error).lower()
    return "429" in text or "rate limit" in text or "ratelimit" in text


class EmbeddingService:
    def __init__(
        self,
        llm: AceAzureFoundry | None = None,
        settings: KnowledgeSettings | None = None,
    ) -> None:
        self._llm = llm or AceAzureFoundry()
        self._settings = settings or get_knowledge_settings()

    @property
    def model(self) -> str:
        return self._llm.embedding_deployment

    def dimensions(self, override: int | None = None) -> int:
        return override or self._settings.embedding_dimensions

    async def _embed_batch(
        self, texts: list[str], dimensions: int | None
    ) -> list[list[float]]:
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                return await self._llm.acreate_embeddings(texts, dimensions=dimensions)
            except Exception as exc:
                last_error = exc
                if _is_rate_limit(exc) and attempt < _MAX_RETRIES - 1:
                    delay = _BASE_DELAY_SECONDS * (2**attempt)
                    logger.warning(
                        "Embedding rate limited; backing off",
                        extra={"attempt": attempt + 1, "delay_seconds": delay},
                    )
                    await asyncio.sleep(delay)
                    continue
                break

        if last_error is not None and _is_rate_limit(last_error):
            raise LLMRateLimitError(cause=last_error) from last_error
        raise EmbeddingError(cause=last_error) from last_error

    async def embed_texts(
        self, texts: list[str], *, dimensions: int | None = None
    ) -> list[list[float]]:
        if not texts:
            return []
        resolved = self.dimensions(dimensions)
        batch_size = max(1, self._settings.embedding_batch_size)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            vectors.extend(await self._embed_batch(batch, resolved))
        return vectors
    
    async def embed_query(
            self, text: str, *, dimensions: int | None = None
    ) -> list[float]:
        vectors = await self.embed_texts([text], dimensions=dimensions)
        if not vectors:
            raise EmbeddingError("No embedding returned for query.")
        return vectors[0]
    
_service: EmbeddingService | None = None

def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        _service = EmbeddingService()
    return _service
