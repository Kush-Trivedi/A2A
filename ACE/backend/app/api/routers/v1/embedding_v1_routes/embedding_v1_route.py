from .....dto.common import ApiEnvelope
from .....utils.common.logger import Logger
from fastapi import APIRouter, Depends, status
from .....security.dependencies import require_csrf
from ....dependencies import provide_embedding_service
from .....security.authorization import require_permission
from .....dto.embedding import EmbeddingRequest, EmbeddingResponse
from .....services.embedding.embedding_service import EmbeddingService


logger = Logger(__name__).get_logger()

embedding_v1_router = APIRouter(prefix="/embeddings", tags=["Embeddings"])


_EMBED_OBJ = "embeddings"


@embedding_v1_router.post(
    "",
    response_model=ApiEnvelope[EmbeddingResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[
        Depends(require_csrf),
        Depends(require_permission(_EMBED_OBJ, "generate")),
    ],
)
async def generate_embeddings(
    payload: EmbeddingRequest,
    service: EmbeddingService = Depends(provide_embedding_service),
) -> ApiEnvelope[EmbeddingResponse]:
    vectors = await service.embed_texts(payload.texts, dimensions=payload.dimensions)
    return ApiEnvelope(
        data=EmbeddingResponse(
            model=service.model,
            dimensions=service.dimensions(payload.dimensions),
            embeddings=vectors,
        )
    )
