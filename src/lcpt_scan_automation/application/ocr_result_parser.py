"""Maps HaulSafe OCR extractedInfo dict → CoverSheet domain model."""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from ..domain.enums import CoverSheetAction, ReviewReasonCode
from ..domain.models import CoverSheet
from ..domain.validation import normalize_checkbox, parse_routing


def parse_ocr_extracted_info(extracted_info: dict[str, Any]) -> tuple[CoverSheet, Optional[ReviewReasonCode]]:
    """Parse the flat extractedInfo dict returned by HaulSafe OCR.

    Returns (cover_sheet, review_reason).
    review_reason is non-None when routing is ambiguous — the caller should
    route to review immediately.

    Field name mapping (HaulSafe → domain):
        companyName                 → company_name
        workRequestNumber           → work_request_number
        routingInternal / External  → routing (via parse_routing)
        processThroughStateAgency   → CoverSheetAction.PROCESS_THROUGH_STATE_AGENCY
        receiveCredentials          → CoverSheetAction.RECEIVE_CREDENTIALS
        complete                    → CoverSheetAction.COMPLETE
        additionalNotes             → additional_notes
        completedBy                 → completed_by
        date                        → date
    """
    routing_value, routing_reason = parse_routing(
        extracted_info.get("routingInternal"),
        extracted_info.get("routingExternal"),
    )

    checked_actions: list[CoverSheetAction] = []
    if normalize_checkbox(extracted_info.get("processThroughStateAgency")):
        checked_actions.append(CoverSheetAction.PROCESS_THROUGH_STATE_AGENCY)
    if normalize_checkbox(extracted_info.get("receiveCredentials")):
        checked_actions.append(CoverSheetAction.RECEIVE_CREDENTIALS)
    if normalize_checkbox(extracted_info.get("complete")):
        checked_actions.append(CoverSheetAction.COMPLETE)

    parsed_date: Optional[date] = _parse_date(extracted_info.get("date"))

    from ..domain.enums import RoutingType

    cover_sheet = CoverSheet(
        company_name=_nonempty(extracted_info.get("companyName")),
        work_request_number=_nonempty(extracted_info.get("workRequestNumber")),
        routing=RoutingType(routing_value) if routing_value else None,
        checked_actions=checked_actions,
        additional_notes=_nonempty(extracted_info.get("additionalNotes")),
        completed_by=_nonempty(extracted_info.get("completedBy")),
        scan_date=parsed_date,
    )

    return cover_sheet, routing_reason


def _nonempty(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _parse_date(raw: Any) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None
