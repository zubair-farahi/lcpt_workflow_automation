class LcptScanError(Exception):
    """Base class for all LCPT scan automation errors."""


class ValidationError(LcptScanError):
    """Cover sheet or input data failed validation."""


class OcrError(LcptScanError):
    """Error communicating with or interpreting results from the OCR service."""


class OcrTimeoutError(OcrError):
    """OCR polling exceeded the configured wait limit."""


class OcrAuthError(OcrError):
    """Authentication failure against the OCR service."""


class CpSuiteError(LcptScanError):
    """Error communicating with the CP Suite API."""


class CpSuiteAuthError(CpSuiteError):
    """Authentication failure against the CP Suite API."""


class CpSuiteNotFoundError(CpSuiteError):
    """Requested resource was not found in CP Suite."""


class StorageError(LcptScanError):
    """Error reading from or writing to storage (local or S3)."""


class PdfProcessingError(LcptScanError):
    """Error splitting or reading a PDF."""


class IdempotencyConflictError(LcptScanError):
    """Scan is already being processed and should not be started again."""


class ReviewQueueError(LcptScanError):
    """Failed to enqueue a scan for human review."""
