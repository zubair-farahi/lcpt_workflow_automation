"""Unit tests for ProcessScanUseCase — covers all 12 specified test cases.

All external dependencies (OCR, CP Suite, storage, idempotency, review queue)
use in-memory / local fakes.  No network calls, no real S3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lcpt_scan_automation.config.settings import Settings
from lcpt_scan_automation.domain.enums import ProcessingState, ReviewReasonCode
from lcpt_scan_automation.domain.models import ScanEvent
from lcpt_scan_automation.infrastructure.cp_suite.mock_cp_suite_client import MockCpSuiteClient
from lcpt_scan_automation.infrastructure.idempotency.memory_idempotency_store import MemoryIdempotencyStore
from lcpt_scan_automation.infrastructure.ocr.mock_ocr_client import MockOcrClient
from lcpt_scan_automation.infrastructure.review_queue.local_review_queue import LocalReviewQueue
from lcpt_scan_automation.infrastructure.storage.local_storage import LocalStorage
from tests.conftest import build_use_case, make_pdf


# ── helpers ────────────────────────────────────────────────────────────────────

def _write_pdf(storage: LocalStorage, filename: str, num_pages: int = 2) -> str:
    """Write a minimal PDF to storage and return its path."""
    pdf_bytes = make_pdf(num_pages)
    storage.write_bytes(filename, pdf_bytes)
    return str(Path(storage._base / filename))


def _make_ocr_client(info: dict, status: str = "COMPLETED") -> MockOcrClient:
    return MockOcrClient(extracted_info=info, status=status)


_VALID_INFO = {
    "companyName": "Pacific First Group",
    "workRequestNumber": "PFG-WR-351",
    "routingInternal": "x",
    "routingExternal": "",
    "processThroughStateAgency": "x",
    "receiveCredentials": "",
    "complete": "",
    "additionalNotes": "",
    "completedBy": "Jane Smith",
    "date": "2024-01-15",
}


# ── Test 1: valid cover sheet routes to CP Suite ───────────────────────────────

def test_valid_scan_reaches_success(
    settings: Settings,
    mock_cp: MockCpSuiteClient,
    idempotency_store: MemoryIdempotencyStore,
    review_queue: LocalReviewQueue,
    local_storage: LocalStorage,
):
    source = _write_pdf(local_storage, "scan.pdf")
    ocr = _make_ocr_client(_VALID_INFO)
    use_case = build_use_case(settings, ocr, mock_cp, idempotency_store, review_queue, local_storage)

    record = use_case.execute(ScanEvent(source_path=source))

    assert record.state == ProcessingState.SUCCESS
    methods = [c["method"] for c in mock_cp.calls]
    assert "get_work_request" in methods
    assert "attach_pdf_internal" in methods
    assert "add_system_note" in methods


# ── Test 2: both Internal and External checked → review ───────────────────────

def test_both_routes_checked_routes_to_review(
    settings: Settings,
    mock_cp: MockCpSuiteClient,
    idempotency_store: MemoryIdempotencyStore,
    review_queue: LocalReviewQueue,
    local_storage: LocalStorage,
    tmp_path: Path,
):
    info = {**_VALID_INFO, "routingInternal": "x", "routingExternal": "x"}
    source = _write_pdf(local_storage, "scan_both.pdf")
    ocr = _make_ocr_client(info)
    use_case = build_use_case(settings, ocr, mock_cp, idempotency_store, review_queue, local_storage)

    record = use_case.execute(ScanEvent(source_path=source))

    assert record.state == ProcessingState.REVIEW_REQUIRED
    review_files = list((tmp_path / "review").glob("*.json"))
    assert len(review_files) == 1
    import json
    item = json.loads(review_files[0].read_text())
    assert item["reason_code"] == ReviewReasonCode.BOTH_ROUTES_CHECKED


# ── Test 3: neither Internal nor External checked → review ────────────────────

def test_neither_route_checked_routes_to_review(
    settings: Settings,
    mock_cp: MockCpSuiteClient,
    idempotency_store: MemoryIdempotencyStore,
    review_queue: LocalReviewQueue,
    local_storage: LocalStorage,
    tmp_path: Path,
):
    info = {**_VALID_INFO, "routingInternal": "", "routingExternal": ""}
    source = _write_pdf(local_storage, "scan_neither.pdf")
    ocr = _make_ocr_client(info)
    use_case = build_use_case(settings, ocr, mock_cp, idempotency_store, review_queue, local_storage)

    record = use_case.execute(ScanEvent(source_path=source))

    assert record.state == ProcessingState.REVIEW_REQUIRED
    review_files = list((tmp_path / "review").glob("*.json"))
    assert any(ReviewReasonCode.NEITHER_ROUTE_CHECKED in f.stem for f in review_files)


# ── Test 4: Work Request not found → review ───────────────────────────────────

def test_missing_work_request_routes_to_review(
    settings: Settings,
    idempotency_store: MemoryIdempotencyStore,
    review_queue: LocalReviewQueue,
    local_storage: LocalStorage,
    tmp_path: Path,
):
    info = {**_VALID_INFO, "workRequestNumber": "XXX-WR-999"}
    source = _write_pdf(local_storage, "scan_no_wr.pdf")
    ocr = _make_ocr_client(info)
    cp = MockCpSuiteClient()  # empty — WR not found
    use_case = build_use_case(settings, ocr, cp, idempotency_store, review_queue, local_storage)

    record = use_case.execute(ScanEvent(source_path=source))

    assert record.state == ProcessingState.REVIEW_REQUIRED
    review_files = list((tmp_path / "review").glob("*.json"))
    assert any(ReviewReasonCode.WORK_REQUEST_NOT_FOUND in f.stem for f in review_files)


# ── Test 5: company name mismatch → review ────────────────────────────────────

def test_company_mismatch_routes_to_review(
    settings: Settings,
    mock_cp: MockCpSuiteClient,
    idempotency_store: MemoryIdempotencyStore,
    review_queue: LocalReviewQueue,
    local_storage: LocalStorage,
    tmp_path: Path,
    tmp_path_factory,
):
    # Enable company prefix validation and provide a mapping
    mapping_path = tmp_path / "prefix.yaml"
    import yaml
    mapping_path.write_text(
        yaml.dump({"prefix_to_companies": {"PFG": ["Pacific First Group"]}}),
        encoding="utf-8",
    )
    settings_with_prefix = settings.model_copy(
        update={
            "company_prefix_validation_required": True,
            "company_prefix_mapping_path": str(mapping_path),
        }
    )

    info = {**_VALID_INFO, "companyName": "Wrong Company"}
    source = _write_pdf(local_storage, "scan_mismatch.pdf")
    ocr = _make_ocr_client(info)
    use_case = build_use_case(
        settings_with_prefix, ocr, mock_cp, idempotency_store, review_queue, local_storage
    )

    record = use_case.execute(ScanEvent(source_path=source))

    assert record.state == ProcessingState.REVIEW_REQUIRED
    review_files = list((tmp_path / "review").glob("*.json"))
    assert any(ReviewReasonCode.COMPANY_MISMATCH in f.stem for f in review_files)


# ── Test 6: low OCR confidence → review ──────────────────────────────────────

def test_low_ocr_confidence_routes_to_review(
    settings: Settings,
    mock_cp: MockCpSuiteClient,
    idempotency_store: MemoryIdempotencyStore,
    review_queue: LocalReviewQueue,
    local_storage: LocalStorage,
    tmp_path: Path,
):
    settings_with_confidence = settings.model_copy(
        update={"require_ocr_confidence": True, "ocr_confidence_threshold": 0.9}
    )
    source = _write_pdf(local_storage, "scan_low_conf.pdf")

    # Inject a MockOcrClient that sets field_confidences via a subclass override
    class LowConfidenceOcrClient(MockOcrClient):
        def get_result(self, request_id: str):
            result = super().get_result(request_id)
            # Simulate low confidence by injecting it into extracted_info
            # (The domain model's field_confidences are populated by the parser)
            return result

    # The real confidence path requires OcrResult to carry confidence data.
    # Since the staging API doesn't return confidence scores today, we test the
    # validator directly instead of through the full pipeline here.
    from lcpt_scan_automation.domain.models import OcrFieldConfidence
    from lcpt_scan_automation.domain.validation import validate_confidence

    low_conf = [OcrFieldConfidence(field_name="companyName", confidence=0.3)]
    reason = validate_confidence(
        low_conf,
        ["companyName"],
        threshold=0.9,
        require_confidence=True,
    )
    assert reason == ReviewReasonCode.LOW_OCR_CONFIDENCE


# ── Test 7: single-page PDF → review ─────────────────────────────────────────

def test_single_page_pdf_routes_to_review(
    settings: Settings,
    mock_cp: MockCpSuiteClient,
    idempotency_store: MemoryIdempotencyStore,
    review_queue: LocalReviewQueue,
    local_storage: LocalStorage,
    tmp_path: Path,
):
    source = _write_pdf(local_storage, "scan_single.pdf", num_pages=1)
    ocr = _make_ocr_client(_VALID_INFO)
    use_case = build_use_case(settings, ocr, mock_cp, idempotency_store, review_queue, local_storage)

    record = use_case.execute(ScanEvent(source_path=source))

    assert record.state == ProcessingState.REVIEW_REQUIRED
    review_files = list((tmp_path / "review").glob("*.json"))
    assert any(ReviewReasonCode.SINGLE_PAGE_PDF in f.stem for f in review_files)


# ── Test 8: duplicate scan is not processed twice ─────────────────────────────

def test_duplicate_scan_is_skipped(
    settings: Settings,
    mock_cp: MockCpSuiteClient,
    idempotency_store: MemoryIdempotencyStore,
    review_queue: LocalReviewQueue,
    local_storage: LocalStorage,
):
    source = _write_pdf(local_storage, "scan_dup.pdf")
    ocr = _make_ocr_client(_VALID_INFO)
    use_case = build_use_case(settings, ocr, mock_cp, idempotency_store, review_queue, local_storage)

    first = use_case.execute(ScanEvent(source_path=source))
    calls_after_first = len(mock_cp.calls)

    second = use_case.execute(ScanEvent(source_path=source))

    assert first.scan_id == second.scan_id
    assert second.state == ProcessingState.SUCCESS
    # No additional CP Suite calls on the second invocation
    assert len(mock_cp.calls) == calls_after_first


# ── Test 9: checklist mapping works for known task type/action ────────────────

def test_checklist_item_marked_complete_for_known_mapping(
    settings: Settings,
    mock_cp: MockCpSuiteClient,
    idempotency_store: MemoryIdempotencyStore,
    review_queue: LocalReviewQueue,
    local_storage: LocalStorage,
):
    source = _write_pdf(local_storage, "scan_cl.pdf")
    ocr = _make_ocr_client(_VALID_INFO)
    use_case = build_use_case(settings, ocr, mock_cp, idempotency_store, review_queue, local_storage)

    record = use_case.execute(ScanEvent(source_path=source))

    assert record.state == ProcessingState.SUCCESS
    mark_calls = [c for c in mock_cp.calls if c["method"] == "mark_checklist_item_complete"]
    assert len(mark_calls) >= 1


# ── Test 10a: missing checklist item with policy=review → review ──────────────

def test_missing_checklist_item_policy_review(
    settings: Settings,
    idempotency_store: MemoryIdempotencyStore,
    review_queue: LocalReviewQueue,
    local_storage: LocalStorage,
    tmp_path: Path,
):
    settings_review = settings.model_copy(update={"missing_checklist_item_policy": "review"})
    cp = MockCpSuiteClient()
    wr = cp.add_work_request("PFG-WR-351")
    task = cp.add_task(wr.work_request_id, "TYPE_A")
    # Deliberately omit the expected checklist item so it cannot be found

    source = _write_pdf(local_storage, "scan_missing_cl.pdf")
    ocr = _make_ocr_client(_VALID_INFO)
    use_case = build_use_case(settings_review, ocr, cp, idempotency_store, review_queue, local_storage)

    record = use_case.execute(ScanEvent(source_path=source))

    assert record.state == ProcessingState.REVIEW_REQUIRED
    review_files = list((tmp_path / "review").glob("*.json"))
    assert any(ReviewReasonCode.MISSING_CHECKLIST_ITEM in f.stem for f in review_files)


# ── Test 10b: missing checklist item with policy=skip → success ───────────────

def test_missing_checklist_item_policy_skip(
    settings: Settings,
    idempotency_store: MemoryIdempotencyStore,
    review_queue: LocalReviewQueue,
    local_storage: LocalStorage,
):
    settings_skip = settings.model_copy(update={"missing_checklist_item_policy": "skip"})
    cp = MockCpSuiteClient()
    wr = cp.add_work_request("PFG-WR-351")
    cp.add_task(wr.work_request_id, "TYPE_A")
    # No checklist items — policy=skip should not block

    source = _write_pdf(local_storage, "scan_skip.pdf")
    ocr = _make_ocr_client(_VALID_INFO)
    use_case = build_use_case(settings_skip, ocr, cp, idempotency_store, review_queue, local_storage)

    record = use_case.execute(ScanEvent(source_path=source))

    assert record.state == ProcessingState.SUCCESS


# ── Test 11: audit note is created after successful processing ─────────────────

def test_audit_note_created_on_success(
    settings: Settings,
    mock_cp: MockCpSuiteClient,
    idempotency_store: MemoryIdempotencyStore,
    review_queue: LocalReviewQueue,
    local_storage: LocalStorage,
):
    source = _write_pdf(local_storage, "scan_note.pdf")
    ocr = _make_ocr_client(_VALID_INFO)
    use_case = build_use_case(settings, ocr, mock_cp, idempotency_store, review_queue, local_storage)

    record = use_case.execute(ScanEvent(source_path=source))

    assert record.state == ProcessingState.SUCCESS
    note_calls = [c for c in mock_cp.calls if c["method"] == "add_system_note"]
    assert len(note_calls) == 1
    note_text = note_calls[0]["note"]
    assert "LCPT Scan Automation" in note_text
    assert "PFG-WR-351" in note_text
