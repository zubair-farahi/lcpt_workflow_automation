"""In-memory idempotency store — for unit tests and local mock runs.

State lives only for the lifetime of the process. Production uses
S3IdempotencyStore (markers in the scan bucket).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ...domain.enums import ProcessingState
from ...domain.models import ScanRecord


class MemoryIdempotencyStore:
    def __init__(self) -> None:
        self._by_id: dict[str, ScanRecord] = {}

    def get(self, scan_id: str) -> Optional[ScanRecord]:
        return self._by_id.get(scan_id)

    def find_by_source(self, source_path: str, etag: Optional[str]) -> Optional[ScanRecord]:
        matches = [
            r for r in self._by_id.values()
            if r.source_path == source_path and (etag is None or r.source_etag == etag)
        ]
        return max(matches, key=lambda r: r.created_at) if matches else None

    def find_by_ocr_request_id(self, ocr_request_id: str) -> Optional[ScanRecord]:
        for r in self._by_id.values():
            if r.ocr_request_id == ocr_request_id:
                return r
        return None

    def save(self, record: ScanRecord) -> None:
        self._by_id[record.scan_id] = record

    def update_state(
        self,
        scan_id: str,
        state: ProcessingState,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        record = self._by_id.get(scan_id)
        if record is None:
            return
        record.state = state
        record.updated_at = datetime.utcnow()
        if metadata:
            record.metadata.update(metadata)
            if metadata.get("ocr_request_id"):
                record.ocr_request_id = str(metadata["ocr_request_id"])
