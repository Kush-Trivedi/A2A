"""Dense embeddings via the Foundry embedding deployment, batched.

The sparse representation costs nothing extra — it is the GENERATED
tsvector column on document_chunks — so this service only produces the
dense vectors. Configuration failures name the exact yaml keys."""

from ...config.application_context import get_application_context
from ...config.settings_validator import PlaceholderPolicy
from ...entity.knowledge import DEFAULT_EMBEDDING_DIMENSIONS
from ...llm.azure_foundry import AceAzureFoundry, get_ace_azure_foundry
from ...utils.common.logger import Logger
from ...utils.errors import EmbeddingError, ValidationError

logger = Logger(__name__).get_logger()

_BATCH_SIZE = 64


class EmbeddingService:
    def __init__(self, foundry: AceAzureFoundry | None = None) -> None:
        self._validate_config()
        self._foundry = foundry or get_ace_azure_foundry()

    @staticmethod
    def _validate_config() -> None:
        foundry_cfg = get_application_context().microsoft["azure"]["azure_foundry"]
        checks = {
            "base_endpoint": foundry_cfg.get("base_endpoint"),
            "api_key": foundry_cfg.get("api_key"),
            "embedding.deployment": (foundry_cfg.get("embedding") or {}).get("deployment"),
            "embedding.api_version": (foundry_cfg.get("embedding") or {}).get("api_version"),
        }
        for key, value in checks.items():
            if not PlaceholderPolicy.is_configured(value):
                raise ValidationError(
                    "The embedding endpoint is not configured. Set "
                    f"microsoft.azure.azure_foundry.{key} in the env yaml."
                )

    @property
    def model_name(self) -> str:
        return str(self._foundry.embedding_deployment)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Vectors for every text, in order, batched against the API."""
        if not texts:
            return []
        vectors: list[list[float]] = []
        try:
            for start in range(0, len(texts), _BATCH_SIZE):
                batch = texts[start : start + _BATCH_SIZE]
                vectors.extend(await self._foundry.acreate_embeddings(batch))
        except Exception as exc:
            raise EmbeddingError(
                "Embedding call failed — check the Foundry endpoint, api key, "
                "and embedding deployment.",
                cause=exc,
            ) from exc
        for vector in vectors:
            if len(vector) != DEFAULT_EMBEDDING_DIMENSIONS:
                raise EmbeddingError(
                    f"The embedding deployment returned {len(vector)} dimensions "
                    f"but the vector columns are {DEFAULT_EMBEDDING_DIMENSIONS}. "
                    "Set embedding.embedding_dimensions in the env yaml to "
                    f"{DEFAULT_EMBEDDING_DIMENSIONS}."
                )
        return vectors


_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        _service = EmbeddingService()
    return _service
