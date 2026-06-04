"""Pure validation functions — no I/O, no external dependencies."""

from __future__ import annotations

import re
from typing import Optional

from .enums import ReviewReasonCode
from .models import OcrFieldConfidence

# Default WR number pattern. Override via WR_NUMBER_PATTERN env var.
DEFAULT_WR_PATTERN: re.Pattern[str] = re.compile(r"^[A-Z]{2,6}-WR-\d+$")

# Values that indicate a checkbox is ticked.
_CHECKED_VALUES: frozenset[str] = frozenset(
    {"true", "yes", "checked", "x", "☑", "1"}
)


def normalize_checkbox(value: object) -> bool:
    """Return True if *value* represents a checked checkbox."""
    if value is None:
        return False
    return str(value).strip().lower() in _CHECKED_VALUES


def parse_routing(
    internal_raw: Optional[str],
    external_raw: Optional[str],
) -> tuple[Optional[str], Optional[ReviewReasonCode]]:
    """Resolve routing from the two raw checkbox values.

    Returns (routing_value, reason_code).  reason_code is non-None only when
    routing cannot be unambiguously determined.
    """
    internal = normalize_checkbox(internal_raw)
    external = normalize_checkbox(external_raw)

    if internal and external:
        return None, ReviewReasonCode.BOTH_ROUTES_CHECKED
    if not internal and not external:
        return None, ReviewReasonCode.NEITHER_ROUTE_CHECKED

    from .enums import RoutingType

    return (RoutingType.INTERNAL if internal else RoutingType.EXTERNAL), None


def validate_wr_number(
    wr_number: Optional[str],
    pattern: re.Pattern[str] = DEFAULT_WR_PATTERN,
) -> Optional[ReviewReasonCode]:
    if not wr_number or not wr_number.strip():
        return ReviewReasonCode.MISSING_REQUIRED_FIELD
    if not pattern.match(wr_number.strip()):
        return ReviewReasonCode.INVALID_WR_NUMBER_FORMAT
    return None


def validate_company_name(company_name: Optional[str]) -> Optional[ReviewReasonCode]:
    if not company_name or not company_name.strip():
        return ReviewReasonCode.MISSING_REQUIRED_FIELD
    return None


def validate_confidence(
    field_confidences: list[OcrFieldConfidence],
    required_fields: list[str],
    threshold: float,
    require_confidence: bool,
) -> Optional[ReviewReasonCode]:
    """Route to review if any required field confidence is below threshold.

    When require_confidence is False or no confidence data is present, this
    function always returns None (no penalty for missing scores).
    """
    if not require_confidence or not field_confidences:
        return None
    confidence_map = {fc.field_name: fc.confidence for fc in field_confidences}
    for field in required_fields:
        conf = confidence_map.get(field)
        if conf is not None and conf < threshold:
            return ReviewReasonCode.LOW_OCR_CONFIDENCE
    return None


def validate_company_prefix(
    company_name: str,
    wr_number: str,
    prefix_mapping: dict[str, list[str]],
    validation_required: bool,
) -> Optional[ReviewReasonCode]:
    """Check that company_name is consistent with the WR number prefix.

    Returns None when validation is disabled or when the mapping has no entry
    for this prefix (non-blocking by design — see company_prefix_validation_required).
    """
    if not validation_required or not prefix_mapping:
        return None
    prefix = wr_number.split("-WR-")[0] if "-WR-" in wr_number else ""
    expected = prefix_mapping.get(prefix, [])
    if expected and company_name.strip().lower() not in {c.lower() for c in expected}:
        return ReviewReasonCode.COMPANY_MISMATCH
    return None
