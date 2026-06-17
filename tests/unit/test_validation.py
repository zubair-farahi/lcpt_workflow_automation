"""Unit tests for domain/validation.py -- pure functions, no I/O."""

import pytest

from lcpt_scan_automation.domain.enums import ReviewReasonCode, RoutingType
from lcpt_scan_automation.domain.models import OcrFieldConfidence
from lcpt_scan_automation.domain.validation import (
    normalize_checkbox,
    parse_routing,
    validate_confidence,
    validate_wr_number,
)


class TestNormalizeCheckbox:
    @pytest.mark.parametrize(
        "value",
        ["true", "True", "TRUE", "yes", "YES", "x", "X", "☑", "1", "checked", "CHECKED"],
    )
    def test_checked_values(self, value):
        assert normalize_checkbox(value) is True

    @pytest.mark.parametrize(
        "value", [None, "", "false", "False", "no", "0", "unchecked", "NO"]
    )
    def test_unchecked_values(self, value):
        assert normalize_checkbox(value) is False


class TestParseRouting:
    """Routing is derived from the two 'attach' checklist items on the
    cover sheet -- parse_routing is the pure-function core of that logic."""

    def test_internal_only(self):
        routing, reason = parse_routing("x", "")
        assert routing == RoutingType.INTERNAL
        assert reason is None

    def test_external_only(self):
        routing, reason = parse_routing("", "x")
        assert routing == RoutingType.EXTERNAL
        assert reason is None

    def test_both_checked_routes_to_review(self):
        routing, reason = parse_routing("x", "x")
        assert routing is None
        assert reason == ReviewReasonCode.BOTH_ROUTES_CHECKED

    def test_neither_checked_routes_to_review(self):
        routing, reason = parse_routing("", "")
        assert routing is None
        assert reason == ReviewReasonCode.NEITHER_ROUTE_CHECKED

    def test_none_inputs(self):
        routing, reason = parse_routing(None, None)
        assert reason == ReviewReasonCode.NEITHER_ROUTE_CHECKED


class TestValidateWrNumber:
    @pytest.mark.parametrize("wr", ["PFG-WR-351", "AB-WR-1", "ABCDEF-WR-99999"])
    def test_valid_numbers(self, wr):
        assert validate_wr_number(wr) is None

    @pytest.mark.parametrize("wr", [None, "", "  "])
    def test_missing_wr_number(self, wr):
        assert validate_wr_number(wr) == ReviewReasonCode.MISSING_REQUIRED_FIELD

    @pytest.mark.parametrize(
        "wr", ["PFG351", "PFG-WR", "pfg-wr-351", "PFG-WR-abc", "WR-351"]
    )
    def test_invalid_format(self, wr):
        assert validate_wr_number(wr) == ReviewReasonCode.INVALID_WR_NUMBER_FORMAT


class TestValidateConfidence:
    def test_no_confidence_data_always_passes(self):
        result = validate_confidence(
            [], ["workRequestNumber"], threshold=0.9, require_confidence=True
        )
        assert result is None

    def test_require_false_always_passes(self):
        low_conf = [OcrFieldConfidence(field_name="workRequestNumber", confidence=0.1)]
        result = validate_confidence(
            low_conf, ["workRequestNumber"], threshold=0.9, require_confidence=False
        )
        assert result is None

    def test_low_confidence_routes_to_review(self):
        low_conf = [OcrFieldConfidence(field_name="workRequestNumber", confidence=0.5)]
        result = validate_confidence(
            low_conf, ["workRequestNumber"], threshold=0.7, require_confidence=True
        )
        assert result == ReviewReasonCode.LOW_OCR_CONFIDENCE

    def test_sufficient_confidence_passes(self):
        high_conf = [OcrFieldConfidence(field_name="workRequestNumber", confidence=0.95)]
        result = validate_confidence(
            high_conf, ["workRequestNumber"], threshold=0.7, require_confidence=True
        )
        assert result is None


# ── normalize_checkbox: OCR output-shape variants (regression 2026-06-10) ──
# HaulSafe sometimes echoes the full line instead of a normalized Yes/No.

@pytest.mark.parametrize(
    "value,expected",
    [
        # Shape 1: normalized booleans
        ("Yes", True), ("No", False), ("True", True), ("Off", False),
        # Shape 2: bare markers
        ("x", True), ("X", True), ("☑", True), ("1", True), ("0", False),
        # Shape 3: whole-line echoes (the 2026-06-10 incident)
        ("[X] Attach documents to Internal Attachments", True),
        ("[x] Receive Credentials", True),
        ("[ ] Attach documents to Attachments", False),
        ("[ ] Send Credentials", False),
        ("[] Process Through State Agency", False),
        ("(X) Internal", True),
        ("( ) External", False),
        # Edge cases
        (None, False), ("", False), ("   ", False),
        ("some random text", False),
    ],
)
def test_normalize_checkbox_tolerates_ocr_variants(value, expected):
    assert normalize_checkbox(value) is expected
