from typing import Any, Optional, Protocol, runtime_checkable

from ..domain.enums import ProcessingState
from ..domain.models import ScanRecord


@runtime_checkable
class IdempotencyStorePort(Protocol):
    def get(self, scan_id: str) -> Optional[ScanRecord]:
        """Fetch a scan record by its scan_id. Returns None if not found."""
        ...

    def find_by_source(
        self,
        source_path: str,
        etag: Optional[str],
    ) -> Optional[ScanRecord]:
        """Find the most recent record for the given source file / etag pair.

        Used to detect duplicate S3 events or re-submitted local files.
        """
        ...

    def find_by_ocr_request_id(self, ocr_request_id: str) -> Optional[ScanRecord]:
        """Find a record by OCR request ID (used for async webhook callbacks)."""
        ...

    def save(self, record: ScanRecord) -> None:
        """Insert or replace a scan record."""
        ...

    def update_state(
        self,
        scan_id: str,
        state: ProcessingState,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Update the state and optionally merge additional metadata fields."""
        ...
