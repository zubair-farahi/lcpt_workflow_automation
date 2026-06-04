"""Real HaulSafe OCR client using the staging API.

Endpoints (confirmed from staging):
  POST /open-api/ocr/extract           — submit document for extraction
  GET  /open-api/ocr/extract/{id}      — poll for result

Auth: api-key header (value from HAUL_OCR_API_KEY env var via Settings).

TODO: confirm exact status enum values from Swagger once accessible.
TODO: confirm whether documentUrl must be public or can be a presigned S3 URL.
TODO: confirm whether HaulSafe returns confidence scores alongside extractedInfo.
TODO: confirm whether a webhook callback mode is supported in production.
"""

from __future__ import annotations

import structlog

import httpx

from ...domain.errors import OcrAuthError, OcrError
from ...domain.models import OcrField, OcrResult, OcrSubmissionResult

log = structlog.get_logger()


class HaulSafeOcrClient:
    """HTTP client for the HaulSafe OCR staging API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        # api_key is intentionally not logged anywhere in this class
        self._client = httpx.Client(
            headers={
                "api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=timeout_seconds,
        )

    def submit_document(
        self,
        document_url: str,
        fields: list[OcrField],
    ) -> OcrSubmissionResult:
        payload = {
            "documentUrl": document_url,
            "fields": [
                {"fieldName": f.field_name, "fieldType": f.field_type}
                for f in fields
            ],
        }
        try:
            response = self._client.post(f"{self._base}/open-api/ocr/extract", json=payload)
        except httpx.TimeoutException as exc:
            raise OcrError(f"HaulSafe OCR submit timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise OcrError(f"HaulSafe OCR network error on submit: {exc}") from exc

        self._raise_for_status(response)
        data = self._parse_json(response)

        request_id = data.get("requestId") or data.get("request_id", "")
        status = data.get("status", "")
        if not request_id:
            raise OcrError(f"HaulSafe OCR submit response missing requestId: {data}")

        log.info("ocr_submit_response", request_id=request_id, status=status)
        return OcrSubmissionResult(request_id=request_id, status=status)

    def get_result(self, request_id: str) -> OcrResult:
        try:
            response = self._client.get(
                f"{self._base}/open-api/ocr/extract/{request_id}"
            )
        except httpx.TimeoutException as exc:
            raise OcrError(f"HaulSafe OCR get_result timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise OcrError(f"HaulSafe OCR network error on get_result: {exc}") from exc

        self._raise_for_status(response)
        data = self._parse_json(response)

        return OcrResult(
            request_id=data.get("requestId") or data.get("request_id", request_id),
            status=data.get("status", ""),
            extracted_info=data.get("extractedInfo") or data.get("extracted_info") or {},
            created_at=data.get("createdAt"),
            updated_at=data.get("updatedAt"),
            raw_response=data,
        )

    def _raise_for_status(self, response: httpx.Response) -> None:
        code = response.status_code
        if code == 401:
            raise OcrAuthError("HaulSafe OCR authentication failed (401). Check HAUL_OCR_API_KEY.")
        if code == 400:
            raise OcrError(f"HaulSafe OCR validation error (400): {response.text[:500]}")
        if code == 429:
            raise OcrError("HaulSafe OCR rate limit exceeded (429). Back off and retry.")
        if code >= 500:
            raise OcrError(f"HaulSafe OCR server error ({code}): {response.text[:500]}")
        if not response.is_success:
            raise OcrError(f"HaulSafe OCR unexpected status {code}: {response.text[:500]}")

    @staticmethod
    def _parse_json(response: httpx.Response) -> dict:
        try:
            return response.json()
        except Exception as exc:
            raise OcrError(
                f"HaulSafe OCR returned non-JSON response ({response.status_code}): "
                f"{response.text[:200]}"
            ) from exc
