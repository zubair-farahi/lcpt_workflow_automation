"""Unit tests for ProcessScanUseCase -- covers the core test cases.

All external dependencies (OCR, CP Suite, storage, idempotency, review queue)
use in-memory / local fakes. No network calls, no real S3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lcpt_scan_automation.config.settings import Settings
from lcpt_scan_automation.domain.enums import ProcessingState, ReviewReasonCode
from lcpt_scan_automation.domain.models import ScanEvent
from lcpt_scan_automation.infrastructure.idempotency.memory_idempotency_store import MemoryIdempotencyStore
from tests.conftest import LocalStorage, MockCpSuiteClient, MockOcrClient, build_use_case, make_pdf


# ── helpers ───────────────────────────────────────────────────────────────────

def _write_pdf(storage: LocalStorage, filename: str, num_pages: int = 2) -> str:
    pdf_bytes = make_pdf(num_pages)
    storage.write_bytes(filename, pdf_bytes)
    return str(Path(storage._base / filename))


def _make_ocr_client(info: dict, status: str = "COMPLETED") -> MockOcrClient:
    return MockOcrClient(extracted_info=info, status=status)


# A cover sheet with: routing=INTERNAL (only "Attach to Internal" checked),
# one checklist action checked (Process Through State Agency).
_VALID_INFO = {
    "workRequestNumber": "PFG-WR-351",
    "attachDocumentsToInternalAttachments": "x",
    "attachDocumentsToAttachments": "",
    "processThroughStateAgency": "x",
    "receiveCredentials": "",
    "sendCredentials": "",
    "additionalNotes": "",
    "completedBy": "Jane Smith",
    "date": "2024-01-15",
}


def test_valid_scan_reaches_success(settings, mock_cp, idempotency_store, review_queue, local_storage):
    source = _write_pdf(local_storage, "scan.pdf")
    ocr = _make_ocr_client(_VALID_INFO)
    use_case = build_use_case(settings, ocr, mock_cp, idempotency_store, review_queue, local_storage)
    record = use_case.execute(ScanEvent(source_path=source))
    assert record.state == ProcessingState.SUCCESS
    methods = [c["method"] for c in mock_cp.calls]
    assert "get_work_request" in methods
    assert "attach_pdf_internal" in methods
    assert "add_system_note" in methods


def test_both_routes_checked_routes_to_review(settings, mock_cp, idempotency_store, review_queue, local_storage, tmp_path):
    info = {**_VALID_INFO, "attachDocumentsToInternalAttachments": "x", "attachDocumentsToAttachments": "x"}
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


def test_neither_route_checked_routes_to_review(settings, mock_cp, idempotency_store, review_queue, local_storage, tmp_path):
    info = {**_VALID_INFO, "attachDocumentsToInternalAttachments": "", "attachDocumentsToAttachments": ""}
    source = _write_pdf(local_storage, "scan_neither.pdf")
    ocr = _make_ocr_client(info)
    use_case = build_use_case(settings, ocr, mock_cp, idempotency_store, review_queue, local_storage)
    record = use_case.execute(ScanEvent(source_path=source))
    assert record.state == ProcessingState.REVIEW_REQUIRED
    review_files = list((tmp_path / "review").glob("*.json"))
    assert any(ReviewReasonCode.NEITHER_ROUTE_CHECKED in f.stem for f in review_files)


def test_missing_work_request_routes_to_review(settings, idempotency_store, review_queue, local_storage, tmp_path):
    info = {**_VALID_INFO, "workRequestNumber": "XXX-WR-999"}
    source = _write_pdf(local_storage, "scan_no_wr.pdf")
    ocr = _make_ocr_client(info)
    cp = MockCpSuiteClient()
    use_case = build_use_case(settings, ocr, cp, idempotency_store, review_queue, local_storage)
    record = use_case.execute(ScanEvent(source_path=source))
    assert record.state == ProcessingState.REVIEW_REQUIRED
    review_files = list((tmp_path / "review").glob("*.json"))
    assert any(ReviewReasonCode.WORK_REQUEST_NOT_FOUND in f.stem for f in review_files)


def test_low_ocr_confidence_routes_to_review():
    from lcpt_scan_automation.domain.models import OcrFieldConfidence
    from lcpt_scan_automation.domain.validation import validate_confidence
    low_conf = [OcrFieldConfidence(field_name="workRequestNumber", confidence=0.3)]
    reason = validate_confidence(low_conf, ["workRequestNumber"], threshold=0.9, require_confidence=True)
    assert reason == ReviewReasonCode.LOW_OCR_CONFIDENCE


def test_single_page_pdf_routes_to_review(settings, mock_cp, idempotency_store, review_queue, local_storage, tmp_path):
    source = _write_pdf(local_storage, "scan_single.pdf", num_pages=1)
    ocr = _make_ocr_client(_VALID_INFO)
    use_case = build_use_case(settings, ocr, mock_cp, idempotency_store, review_queue, local_storage)
    record = use_case.execute(ScanEvent(source_path=source))
    assert record.state == ProcessingState.REVIEW_REQUIRED
    review_files = list((tmp_path / "review").glob("*.json"))
    assert any(ReviewReasonCode.SINGLE_PAGE_PDF in f.stem for f in review_files)


def test_duplicate_scan_is_skipped(settings, mock_cp, idempotency_store, review_queue, local_storage):
    source = _write_pdf(local_storage, "scan_dup.pdf")
    ocr = _make_ocr_client(_VALID_INFO)
    use_case = build_use_case(settings, ocr, mock_cp, idempotency_store, review_queue, local_storage)
    first = use_case.execute(ScanEvent(source_path=source))
    calls_after_first = len(mock_cp.calls)
    second = use_case.execute(ScanEvent(source_path=source))
    assert first.scan_id == second.scan_id
    assert second.state == ProcessingState.SUCCESS
    assert len(mock_cp.calls) == calls_after_first


def test_checklist_item_marked_complete_for_known_mapping(settings, mock_cp, idempotency_store, review_queue, local_storage):
    source = _write_pdf(local_storage, "scan_cl.pdf")
    ocr = _make_ocr_client(_VALID_INFO)
    use_case = build_use_case(settings, ocr, mock_cp, idempotency_store, review_queue, local_storage)
    record = use_case.execute(ScanEvent(source_path=source))
    assert record.state == ProcessingState.SUCCESS
    mark_calls = [c for c in mock_cp.calls if c["method"] == "mark_checklist_item_complete"]
    assert len(mark_calls) >= 1


def test_missing_checklist_item_policy_review(settings, idempotency_store, review_queue, local_storage, tmp_path):
    settings_review = settings.model_copy(update={"missing_checklist_item_policy": "review"})
    cp = MockCpSuiteClient()
    wr = cp.add_work_request("PFG-WR-351")
    cp.add_task(wr.work_request_id, "TYPE_A")
    source = _write_pdf(local_storage, "scan_missing_cl.pdf")
    ocr = _make_ocr_client(_VALID_INFO)
    use_case = build_use_case(settings_review, ocr, cp, idempotency_store, review_queue, local_storage)
    record = use_case.execute(ScanEvent(source_path=source))
    assert record.state == ProcessingState.REVIEW_REQUIRED
    review_files = list((tmp_path / "review").glob("*.json"))
    assert any(ReviewReasonCode.MISSING_CHECKLIST_ITEM in f.stem for f in review_files)


def test_missing_checklist_item_policy_skip(settings, idempotency_store, review_queue, local_storage):
    settings_skip = settings.model_copy(update={"missing_checklist_item_policy": "skip"})
    cp = MockCpSuiteClient()
    wr = cp.add_work_request("PFG-WR-351")
    cp.add_task(wr.work_request_id, "TYPE_A")
    source = _write_pdf(local_storage, "scan_skip.pdf")
    ocr = _make_ocr_client(_VALID_INFO)
    use_case = build_use_case(settings_skip, ocr, cp, idempotency_store, review_queue, local_storage)
    record = use_case.execute(ScanEvent(source_path=source))
    assert record.state == ProcessingState.SUCCESS


def test_audit_note_created_on_success(settings, mock_cp, idempotency_store, review_queue, local_storage):
    source = _write_pdf(local_storage, "scan_note.pdf")
    ocr = _make_ocr_client(_VALID_INFO)
    use_case = build_use_case(settings, ocr, mock_cp, idempotency_store, review_queue, local_storage)
    record = use_case.execute(ScanEvent(source_path=source))
    assert record.state == ProcessingState.SUCCESS
    note_calls = [c for c in mock_cp.calls if c["method"] == "add_system_note"]
    assert len(note_calls) == 1
    assert "LCPT Scan Automation" in note_calls[0]["note"]
    assert "PFG-WR-351" in note_calls[0]["note"]
