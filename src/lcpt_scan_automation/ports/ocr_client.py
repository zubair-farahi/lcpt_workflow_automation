from typing import Protocol, runtime_checkable

from ..domain.models import OcrField, OcrResult, OcrSubmissionResult


@runtime_checkable
class OcrClientPort(Protocol):
    def submit_document(
        self,
        document_url: str,
        fields: list[OcrField],
    ) -> OcrSubmissionResult:
        """Submit a document URL for OCR extraction.

        Returns a submission result containing the request_id and initial status.
        """
        ...

    def get_result(self, request_id: str) -> OcrResult:
        """Fetch the current OCR result for the given request_id.

        The caller is responsible for polling until status is terminal
        (COMPLETED or FAILED).
        """
        ...
