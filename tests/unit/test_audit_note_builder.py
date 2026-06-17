"""Unit tests for AuditNoteBuilder."""

from datetime import date

from lcpt_scan_automation.application.audit_note_builder import AuditNoteBuilder
from lcpt_scan_automation.domain.enums import CoverSheetAction, ProcessingState, RoutingType
from lcpt_scan_automation.domain.models import CoverSheet, ScanRecord


def _make_record() -> ScanRecord:
    return ScanRecord(
        scan_id="test-scan-001",
        source_path="/scans/packet.pdf",
        correlation_id="test-scan-001",
        state=ProcessingState.SUCCESS,
    )


def _make_cover_sheet() -> CoverSheet:
    return CoverSheet(
        work_request_number="PFG-WR-351",
        routing=RoutingType.INTERNAL,
        checked_actions=[CoverSheetAction.PROCESS_THROUGH_STATE_AGENCY],
        completed_by="Jane Smith",
        scan_date=date(2024, 1, 15),
    )


class TestAuditNoteBuilder:
    def test_note_contains_automation_name(self):
        note = AuditNoteBuilder().build(
            _make_record(), _make_cover_sheet(),
            RoutingType.INTERNAL,
            [CoverSheetAction.PROCESS_THROUGH_STATE_AGENCY],
            "req-123",
        )
        assert "LCPT Scan Automation" in note

    def test_note_contains_scan_id(self):
        note = AuditNoteBuilder().build(
            _make_record(), _make_cover_sheet(),
            RoutingType.INTERNAL,
            [CoverSheetAction.PROCESS_THROUGH_STATE_AGENCY],
            "req-123",
        )
        assert "test-scan-001" in note

    def test_note_contains_wr_number(self):
        note = AuditNoteBuilder().build(
            _make_record(), _make_cover_sheet(),
            RoutingType.INTERNAL,
            [CoverSheetAction.PROCESS_THROUGH_STATE_AGENCY],
            "req-123",
        )
        assert "PFG-WR-351" in note

    def test_note_contains_routing(self):
        note = AuditNoteBuilder().build(
            _make_record(), _make_cover_sheet(),
            RoutingType.EXTERNAL,
            [],
            "req-456",
        )
        assert "EXTERNAL" in note

    def test_note_contains_ocr_request_id(self):
        note = AuditNoteBuilder().build(
            _make_record(), _make_cover_sheet(),
            RoutingType.INTERNAL,
            [],
            "my-ocr-req-id",
        )
        assert "my-ocr-req-id" in note

    def test_note_contains_completed_by(self):
        note = AuditNoteBuilder().build(
            _make_record(), _make_cover_sheet(),
            RoutingType.INTERNAL,
            [],
            "req-123",
        )
        assert "Jane Smith" in note
