"""S3-backed idempotency store — no database, S3 itself is the memory.

Layout inside the scan bucket (prefix configurable, default "state/"):

    state/scans/{scan_id}.json          full ScanRecord
    state/index/source/{hash}.json      {"scan_id": ...}  hash = sha256(path|etag)
    state/index/ocr/{request_id}.json   {"scan_id": ...}

Why this replaces SQLite: the previous store was a local file on one
machine — invisible to Lambda, lost on disk wipe. Markers in S3 are
durable, shared by every runner (laptop watcher, Lambda), and need no
infrastructure beyond the bucket we already use.

Failure semantics: a missing marker means "not processed" (the file will
be processed). Transient S3 errors are raised, never silently treated as
"not processed", so an S3 outage cannot cause duplicate CP Suite writes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Optional

import structlog

from ...domain.enums import ProcessingState
from ...domain.errors import StorageError
from ...domain.models import ScanRecord
from ...ports.storage import StoragePort

log = structlog.get_logger()


class S3IdempotencyStore:
    def __init__(self, storage: StoragePort, prefix: str = "state/") -> None:
        self._storage = storage
        self._prefix = prefix.strip("/") + "/"

    # ── port implementation ─────────────────────────────────────────────

    def get(self, scan_id: str) -> Optional[ScanRecord]:
        raw = self._read_optional(self._record_key(scan_id))
        return ScanRecord.model_validate_json(raw) if raw else None

    def find_by_source(self, source_path: str, etag: Optional[str]) -> Optional[ScanRecord]:
        raw = self._read_optional(self._source_index_key(source_path, etag))
        if not raw:
            return None
        scan_id = json.loads(raw).get("scan_id", "")
        return self.get(scan_id) if scan_id else None

    def find_by_ocr_request_id(self, ocr_request_id: str) -> Optional[ScanRecord]:
        raw = self._read_optional(f"{self._prefix}index/ocr/{ocr_request_id}.json")
        if not raw:
            return None
        scan_id = json.loads(raw).get("scan_id", "")
        return self.get(scan_id) if scan_id else None

    def save(self, record: ScanRecord) -> None:
        self._write_json(self._record_key(record.scan_id), record.model_dump_json())
        self._write_json(
            self._source_index_key(record.source_path, record.source_etag),
            json.dumps({"scan_id": record.scan_id}),
        )
        if record.ocr_request_id:
            self._write_json(
                f"{self._prefix}index/ocr/{record.ocr_request_id}.json",
                json.dumps({"scan_id": record.scan_id}),
            )

    def update_state(
        self,
        scan_id: str,
        state: ProcessingState,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        record = self.get(scan_id)
        if record is None:
            log.warning("s3_state_update_missing_record", scan_id=scan_id, state=str(state))
            return
        record.state = state
        record.updated_at = datetime.utcnow()
        if metadata:
            record.metadata.update(metadata)
            ocr_id = metadata.get("ocr_request_id")
            if ocr_id:
                record.ocr_request_id = str(ocr_id)
                self._write_json(
                    f"{self._prefix}index/ocr/{ocr_id}.json",
                    json.dumps({"scan_id": scan_id}),
                )
        self._write_json(self._record_key(scan_id), record.model_dump_json())

    # ── helpers ─────────────────────────────────────────────────────────

    def _record_key(self, scan_id: str) -> str:
        return f"{self._prefix}scans/{scan_id}.json"

    def _source_index_key(self, source_path: str, etag: Optional[str]) -> str:
        digest = hashlib.sha256(f"{source_path}|{etag or ''}".encode()).hexdigest()[:32]
        return f"{self._prefix}index/source/{digest}.json"

    def _read_optional(self, key: str) -> Optional[bytes]:
        """Read an object; return None ONLY for a genuinely missing key."""
        try:
            return self._storage.read_bytes(key)
        except StorageError as exc:
            cause = exc.__cause__
            code = ""
            response = getattr(cause, "response", None)
            if isinstance(response, dict):
                code = str(response.get("Error", {}).get("Code", ""))
            if code in ("NoSuchKey", "404", "NotFound") or "NoSuchKey" in str(exc):
                return None
            raise  # transient/other errors must fail loudly, not look like "new file"

    def _write_json(self, key: str, payload: str) -> None:
        self._storage.write_bytes(key, payload.encode("utf-8"))
