"""Test 12: Lambda handler delegates to use case and contains no business logic."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from lcpt_scan_automation.domain.enums import ProcessingState
from lcpt_scan_automation.domain.models import ScanRecord


def _make_s3_event(key: str, etag: str = "abc123") -> dict:
    return {
        "Records": [
            {
                "s3": {
                    "object": {"key": key, "eTag": etag},
                    "bucket": {"name": "test-bucket"},
                }
            }
        ]
    }


def _make_mock_record(state: ProcessingState = ProcessingState.SUCCESS) -> ScanRecord:
    return ScanRecord(
        scan_id="test-scan-001",
        source_path="scans/scan.pdf",
        correlation_id="test-scan-001",
        state=state,
    )


class TestLambdaHandlerDelegation:
    def test_s3_handler_delegates_to_use_case(self):
        """Lambda handler must call execute() and not contain any routing/validation logic."""
        mock_record = _make_mock_record(ProcessingState.SUCCESS)
        mock_use_case = MagicMock()
        mock_use_case.execute.return_value = mock_record

        with patch(
            "lcpt_scan_automation.entrypoints.lambda_handler.build_process_scan_use_case",
            return_value=mock_use_case,
        ):
            from lcpt_scan_automation.entrypoints.lambda_handler import handle_s3_event

            response = handle_s3_event(_make_s3_event("scans/scan.pdf"), {})

        assert response["statusCode"] == 200
        mock_use_case.execute.assert_called_once()
        call_arg = mock_use_case.execute.call_args[0][0]
        assert call_arg.source_path == "scans/scan.pdf"

    def test_s3_handler_returns_200_for_success(self):
        mock_record = _make_mock_record(ProcessingState.SUCCESS)
        mock_use_case = MagicMock()
        mock_use_case.execute.return_value = mock_record

        with patch(
            "lcpt_scan_automation.entrypoints.lambda_handler.build_process_scan_use_case",
            return_value=mock_use_case,
        ):
            from lcpt_scan_automation.entrypoints.lambda_handler import handle_s3_event

            response = handle_s3_event(_make_s3_event("scans/scan.pdf"), {})

        body = json.loads(response["body"])
        assert body["processed"][0]["state"] == ProcessingState.SUCCESS

    def test_s3_handler_returns_200_for_review_required(self):
        """Review-routed scans are not errors from the Lambda perspective."""
        mock_record = _make_mock_record(ProcessingState.REVIEW_REQUIRED)
        mock_use_case = MagicMock()
        mock_use_case.execute.return_value = mock_record

        with patch(
            "lcpt_scan_automation.entrypoints.lambda_handler.build_process_scan_use_case",
            return_value=mock_use_case,
        ):
            from lcpt_scan_automation.entrypoints.lambda_handler import handle_s3_event

            response = handle_s3_event(_make_s3_event("scans/scan.pdf"), {})

        assert response["statusCode"] == 200

    def test_s3_handler_skips_records_without_key(self):
        event = {"Records": [{"s3": {"object": {}, "bucket": {"name": "test-bucket"}}}]}
        mock_use_case = MagicMock()

        with patch(
            "lcpt_scan_automation.entrypoints.lambda_handler.build_process_scan_use_case",
            return_value=mock_use_case,
        ):
            from lcpt_scan_automation.entrypoints.lambda_handler import handle_s3_event

            response = handle_s3_event(event, {})

        mock_use_case.execute.assert_not_called()
        assert response["statusCode"] == 200

    def test_ocr_webhook_handler_delegates_to_use_case(self):
        mock_record = _make_mock_record(ProcessingState.SUCCESS)
        mock_use_case = MagicMock()
        mock_use_case.execute.return_value = mock_record

        payload = json.dumps({
            "requestId": "req-abc",
            "status": "COMPLETED",
            "extractedInfo": {"companyName": "Test Co"},
        })

        with patch(
            "lcpt_scan_automation.entrypoints.lambda_handler.build_handle_ocr_callback_use_case",
            return_value=mock_use_case,
        ):
            from lcpt_scan_automation.entrypoints.lambda_handler import handle_ocr_webhook

            response = handle_ocr_webhook({"body": payload}, {})

        assert response["statusCode"] == 200
        mock_use_case.execute.assert_called_once()
