"""Unit tests for domain/validation.py — pure functions, no I/O."""

import pytest

from lcpt_scan_automation.domain.enums import ReviewReasonCode, RoutingType
from lcpt_scan_automation.domain.models import OcrFieldConfidence
from lcpt_scan_automation.domain.validation import (
    normalize_checkbox,
    parse_routing,
    validate_company_name,
    validate_company_prefix,
    validate_confidence,
    validate_wr_number,
)


class TestNormalizeCheckbox:
    @pytest.mark.parametrize("value", ["true", "True", "TRUE", "yes", "YES", "x", "X", "☑", "1", "checked", "CHECKED"])
    def test_checked_values(self, value):
        assert normalize_checkbox(value) is True

    @pytest.mark.parametrize("value", [None, "", "false", "False", "no", "0", "unchecked", "NO"])
    def test_unchecked_values(self, value):
        assert normalize_checkbox(value) is False


class TestParseRouting:
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

    @pytest.mark.parametrize("wr", ["PFG351", "PFG-WR", "pfg-wr-351", "PFG-WR-abc", "WR-351"])
    def test_invalid_format(self, wr):
        assert validate_wr_number(wr) == ReviewReasonCode.INVALID_WR_NUMBER_FORMAT


class TestValidateCompanyName:
    def test_valid_name(self):
        assert validate_company_name("Pacific First Group") is None

    @pytest.mark.parametrize("name", [None, "", "   "])
    def test_missing_name(self, name):
        assert validate_company_name(name) == ReviewReasonCode.MISSING_REQUIRED_FIELD


class TestValidateConfidence:
    def test_no_confidence_data_always_passes(self):
        result = validate_confidence([], ["companyName"], threshold=0.9, require_confidence=True)
        assert result is None

    def test_require_false_always_passes(self):
        low_conf = [OcrFieldConfidence(field_name="companyName", confidence=0.1)]
        result = validate_confidence(low_conf, ["companyName"], threshold=0.9, require_confidence=False)
        assert result is None

    def test_low_confidence_routes_to_review(self):
        low_conf = [OcrFieldConfidence(field_name="companyName", confidence=0.5)]
        result = validate_confidence(low_conf, ["companyName"], threshold=0.7, require_confidence=True)
        assert result == ReviewReasonCode.LOW_OCR_CONFIDENCE

    def test_sufficient_confidence_passes(self):
        high_conf = [OcrFieldConfidence(field_name="companyName", confidence=0.95)]
        result = validate_confidence(high_conf, ["companyName"], threshold=0.7, require_confidence=True)
        assert result is None


class TestValidateCompanyPrefix:
    _MAPPING = {"PFG": ["Pacific First Group", "PFG Inc"]}

    def test_matching_company_passes(self):
        result = validate_company_prefix("Pacific First Group", "PFG-WR-351", self._MAPPING, True)
        assert result is None

    def test_mismatched_company_routes_to_review(self):
        result = validate_company_prefix("Wrong Company", "PFG-WR-351", self._MAPPING, True)
        assert result == ReviewReasonCode.COMPANY_MISMATCH

    def test_validation_disabled_always_passes(self):
        result = validate_company_prefix("Wrong Company", "PFG-WR-351", self._MAPPING, False)
        assert result is None

    def test_missing_prefix_in_mapping_passes(self):
        result = validate_company_prefix("Any Company", "XYZ-WR-100", self._MAPPING, True)
        assert result is None

    def test_case_insensitive_match(self):
        result = validate_company_prefix("pacific first group", "PFG-WR-351", self._MAPPING, True)
        assert result is None
