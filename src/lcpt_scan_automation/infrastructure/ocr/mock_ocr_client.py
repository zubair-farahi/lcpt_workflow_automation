"""Mock OCR client for local testing and unit tests.

Pre-loads a response from a JSON file or accepts an injected dict.
Supports simulating QUEUED → COMPLETED multi-poll scenarios.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from ...domain.models import OcrField, OcrResult, OcrSubmissionResult


class MockOcrClient:
    """Returns canned OCR results without making any HTTP calls.

    Usage in tests:
        client = MockOcrClient.from_file("samples/ocr/valid_cover_sheet_completed.json")

    Usage for simulating failure:
        client = MockOcrClient(status="FAILED")
    """

    def __init__(
        self,
        extracted_info: Optional[dict[str, Any]] = None,
        status: str = "COMPLETED",
        queued_polls: int = 0,
    ) -> None:
        self._extracted_info = extracted_info or {}
        self._final_status = status
        self._queued_polls = queued_polls
        self._poll_count: dict[str, int] = {}

    @classmethod
    def from_file(cls, path: str | Path) -> MockOcrClient:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            extracted_info=data.get("extractedInfo", {}),
            status=data.get("status", "COMPLETED"),
        )

    def submit_document(
        self,
        document_url: str,
        fields: list[OcrField],
    ) -> OcrSubmissionResult:
        request_id = str(uuid4())
        self._poll_count[request_id] = 0
        return OcrSubmissionResult(request_id=request_id, status="QUEUED")

    def get_result(self, request_id: str) -> OcrResult:
        count = self._poll_count.get(request_id, 0)
        self._poll_count[request_id] = count + 1

        if count < self._queued_polls:
            status = "QUEUED"
            info: dict[str, Any] = {}
        else:
            status = self._final_status
            info = self._extracted_info if status == "COMPLETED" else {}

        return OcrResult(
            request_id=request_id,
            status=status,
            extracted_info=info,
            raw_response={"requestId": request_id, "status": status, "extractedInfo": info},
        )
