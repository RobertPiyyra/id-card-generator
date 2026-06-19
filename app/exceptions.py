"""
Application exception hierarchy.
All custom exceptions inherit from AppError for consistent error handling.
"""


class AppError(Exception):
    """Base exception for application errors."""
    status_code = 500

    def __init__(self, message: str = None):
        self.message = message or self.__class__.__name__
        super().__init__(self.message)


class TemplateNotFoundError(AppError):
    """Raised when a template ID does not exist in the database."""
    status_code = 404


class StudentNotFoundError(AppError):
    """Raised when a student ID does not exist in the database."""
    status_code = 404


class PhotoProcessingError(AppError):
    """Raised when photo cropping, resizing, or face detection fails."""
    status_code = 400


class PDFRenderError(AppError):
    """Raised when PDF/vector compilation fails."""
    status_code = 500


class StorageError(AppError):
    """Raised when file upload/download to Cloudinary or local disk fails."""
    status_code = 503


class AuthenticationError(AppError):
    """Raised when login credentials are invalid or session expired."""
    status_code = 401


class AuthorizationError(AppError):
    """Raised when user lacks permission for the requested resource."""
    status_code = 403


class BulkJobError(AppError):
    """Raised when bulk generation job fails or is invalid."""
    status_code = 400


class ImportMappingError(AppError):
    """Raised when Excel column mapping is invalid or missing."""
    status_code = 400


class RateLimitError(AppError):
    """Raised when rate limit is exceeded."""
    status_code = 429


class WebhookDeliveryError(AppError):
    """Raised when webhook delivery fails after retries."""
    status_code = 502


class OCRError(AppError):
    """Raised when text extraction from image fails."""
    status_code = 502


class ArchiveError(AppError):
    """Raised when data archival or restoration fails."""
    status_code = 500
