from datetime import datetime, timezone

from ..domain.enums import CoverSheetAction, RoutingType
from ..domain.models import CoverSheet, ScanRecord

_AUTOMATION_NAME = "LCPT Scan Automation"


class AuditNoteBuilder:
    """Builds the audit/system note written to CP Suite after successful processing."""

    def build(
        self,
        record: ScanRecord,
        cover_sheet: CoverSheet,
        routing: RoutingType,
        checked_actions: list[CoverSheetAction],
        ocr_request_id: str,
    ) -> str:
        actions_str = ", ".join(a.value for a in checked_actions) if checked_actions else "none"
        timestamp = datetime.now(tz=timezone.utc).isoformat()

        lines = [
            f"Automated by: {_AUTOMATION_NAME}",
            f"Scan ID: {record.scan_id}",
            f"Source: {record.source_path}",
            f"Work Request: {cover_sheet.work_request_number}",
            f"Routing: {routing.value}",
            f"Actions: {actions_str}",
            f"OCR Request ID: {ocr_request_id}",
            f"Completed By: {cover_sheet.completed_by or 'N/A'}",
            f"Cover Sheet Date: {cover_sheet.scan_date or 'N/A'}",
            f"Processed At: {timestamp}",
        ]
        return "\n".join(lines)
