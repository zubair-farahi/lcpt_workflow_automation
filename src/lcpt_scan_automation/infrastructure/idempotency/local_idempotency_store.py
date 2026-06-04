import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ...domain.enums import ProcessingState
from ...domain.models import ScanRecord

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS scan_records (
    scan_id            TEXT PRIMARY KEY,
    source_path        TEXT NOT NULL,
    source_etag        TEXT,
    state              TEXT NOT NULL,
    ocr_request_id     TEXT,
    work_request_number TEXT,
    correlation_id     TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    metadata_json      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_source ON scan_records (source_path, source_etag);
CREATE INDEX IF NOT EXISTS idx_ocr_request ON scan_records (ocr_request_id);
"""


class LocalIdempotencyStore:
    """SQLite-backed idempotency store for local development.

    Replace with a DynamoDbIdempotencyStore for Lambda production use.
    """

    def __init__(self, db_path: str | Path = "./data/idempotency.db") -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript(_CREATE_TABLE)
        self._conn.commit()

    def get(self, scan_id: str) -> Optional[ScanRecord]:
        row = self._conn.execute(
            "SELECT * FROM scan_records WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    def find_by_source(self, source_path: str, etag: Optional[str]) -> Optional[ScanRecord]:
        if etag:
            row = self._conn.execute(
                "SELECT * FROM scan_records WHERE source_path = ? AND source_etag = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (source_path, etag),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM scan_records WHERE source_path = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (source_path,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def find_by_ocr_request_id(self, ocr_request_id: str) -> Optional[ScanRecord]:
        row = self._conn.execute(
            "SELECT * FROM scan_records WHERE ocr_request_id = ? LIMIT 1",
            (ocr_request_id,),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def save(self, record: ScanRecord) -> None:
        now = _now()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO scan_records
                (scan_id, source_path, source_etag, state, ocr_request_id,
                 work_request_number, correlation_id, created_at, updated_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.scan_id,
                record.source_path,
                record.source_etag,
                record.state,
                record.ocr_request_id,
                record.work_request_number,
                record.correlation_id,
                record.created_at.isoformat(),
                now,
                json.dumps(record.metadata),
            ),
        )
        self._conn.commit()

    def update_state(
        self,
        scan_id: str,
        state: ProcessingState,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        now = _now()
        existing_row = self._conn.execute(
            "SELECT metadata_json, ocr_request_id FROM scan_records WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()
        if not existing_row:
            return

        merged: dict[str, Any] = json.loads(existing_row[0])
        if metadata:
            merged.update(metadata)

        ocr_request_id = metadata.get("ocr_request_id") if metadata else None
        wr_number = metadata.get("work_request_number") if metadata else None

        self._conn.execute(
            """
            UPDATE scan_records
            SET state = ?, updated_at = ?, metadata_json = ?,
                ocr_request_id = COALESCE(?, ocr_request_id),
                work_request_number = COALESCE(?, work_request_number)
            WHERE scan_id = ?
            """,
            (state, now, json.dumps(merged), ocr_request_id, wr_number, scan_id),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_record(row: tuple) -> ScanRecord:
        (
            scan_id, source_path, source_etag, state, ocr_request_id,
            work_request_number, correlation_id, created_at, updated_at, metadata_json,
        ) = row
        return ScanRecord(
            scan_id=scan_id,
            source_path=source_path,
            source_etag=source_etag,
            state=ProcessingState(state),
            ocr_request_id=ocr_request_id,
            work_request_number=work_request_number,
            correlation_id=correlation_id,
            created_at=datetime.fromisoformat(created_at),
            updated_at=datetime.fromisoformat(updated_at),
            metadata=json.loads(metadata_json),
        )


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
