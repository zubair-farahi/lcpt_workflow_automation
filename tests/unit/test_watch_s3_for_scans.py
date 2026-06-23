from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

from lcpt_scan_automation.domain.enums import ProcessingState
from lcpt_scan_automation.domain.models import ScanRecord
from watch_s3_for_scans import _classify_processed_record


def _record(state=ProcessingState.SUCCESS):
    now = datetime.now(UTC)
    return ScanRecord(
        scan_id="scan-1",
        source_path="test-uploads/cpegg/test-wr-32.pdf",
        source_etag="etag-1",
        state=state,
        correlation_id="scan-1",
        created_at=now,
        updated_at=now + timedelta(seconds=1),
    )


def test_first_success_is_counted_as_processed_even_when_record_was_updated():
    assert _classify_processed_record(_record(), was_seen=False) == "processed"


def test_seen_success_is_counted_as_duplicate():
    assert _classify_processed_record(_record(), was_seen=True) == "duplicate"


def test_first_review_required_is_counted_as_processed():
    assert _classify_processed_record(
        _record(ProcessingState.REVIEW_REQUIRED), was_seen=False
    ) == "processed"


def test_seen_review_required_is_counted_as_duplicate():
    assert _classify_processed_record(
        _record(ProcessingState.REVIEW_REQUIRED), was_seen=True
    ) == "duplicate"
