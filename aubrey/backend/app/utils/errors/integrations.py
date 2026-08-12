from .base import AppError, ErrorCategory

class DatabaseError(AppError):
    http_status = 500
    code = "database_error"
    category = ErrorCategory.DATABASE
    default_message = "A database error occurred."
    expose_message = False

class IntegrityConflictError(DatabaseError):
    http_status = 409
    code = "integrity_conflict"
    category = ErrorCategory.CONFLICT
    default_message = "The operation conflicts with existing data."
    expose_message = True

class ExternalServiceError(AppError):
    http_status = 502
    code = "external_service_error"
    category = ErrorCategory.INTEGRATION
    default_message = "An upstream service failed."
    expose_message = False

class AzureError(ExternalServiceError):
    code = "azure_error"
    category = ErrorCategory.AZURE
    default_message = "An Azure service error occurred."

class KeyVaultError(AzureError):
    code = "key_vault_error"
    default_message = "Failed to access Azure Key Vault."

class SharePointError(ExternalServiceError):
    code = "sharepoint_error"
    category = ErrorCategory.SHAREPOINT
    default_message = "A SharePoint operation failed."

class LLMError(ExternalServiceError):
    code = "llm_error"
    category = ErrorCategory.LLM
    default_message = "The language model request failed."

class LLMTimeoutError(LLMError):
    http_status = 504
    code = "llm_timeout"
    default_message = "The language model request timed out."

class LLMRateLimitError(LLMError):
    http_status = 429
    code = "llm_rate_limited"
    category = ErrorCategory.RATE_LIMIT
    default_message = "The language model is rate limiting requests. Please retry."
    expose_message = True

class EmbeddingError(LLMError):
    code = "embedding_error"
    default_message = "Failed to generate embeddings."

class DocumentProcessingError(AppError):
    http_status = 422
    code = "document_processing_error"
    category = ErrorCategory.VALIDATION
    default_message = "The document could not be processed."
    expose_message = True

class KnowledgeSourceError(AppError):
    http_status = 400
    code = "knowledge_source_error"
    category = ErrorCategory.VALIDATION
    default_message = "The knowledge source is invalid."
    expose_message = True
