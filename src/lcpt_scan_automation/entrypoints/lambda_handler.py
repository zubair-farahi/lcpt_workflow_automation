"""AWS Lambda handler for LCPT Scan Automation.

Two handler functions:
  handle_s3_event    — triggered by S3 ObjectCreated, runs the full pipeline.
  handle_ocr_webhook — triggered by API Gateway (future webhook from HaulSafe).

Business logic lives in ProcessScanUseCase / HandleOcrCallbackUseCase.
This file only parses the event, wires dependencies, and formats the response.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from ..config.settings import Settings, configure_logging
from ..domain.models import OcrResult, ScanEvent
from .container import build_handle_ocr_callback_use_case, build_process_scan_use_case

log = structlog.get_logger()


def handle_s3_event(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entry point for S3 ObjectCreated events."""
    settings = Settings()
    configure_logging(settings)

    results = []
    for record in event.get("Records", []):
        s3_info = record.get("s3", {})
        object_key = s3_info.get("object", {}).get("key", "")
        etag = s3_info.get("object", {}).get("eTag", "")

        if not object_key:
            log.warning("s3_event_missing_key", record=record)
            continue

        scan_event = ScanEvent(source_path=object_key, etag=etag)
        use_case = build_process_scan_use_case(settings, use_s3=True)

        try:
            scan_record = use_case.execute(scan_event)
            results.append({"scan_id": scan_record.scan_id, "state": scan_record.state})
        except Exception as exc:
            log.exception("lambda_s3_handler_error", key=object_key, error=str(exc))
            results.append({"key": object_key, "error": str(exc)})

    return {"statusCode": 200, "body": json.dumps({"processed": results})}


def handle_ocr_webhook(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entry point for HaulSafe OCR webhook callbacks (future async mode).

    Expects the raw HaulSafe callback JSON in event["body"].
    """
    settings = Settings()
    configure_logging(settings)

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError as exc:
        log.error("ocr_webhook_invalid_json", error=str(exc))
        return {"statusCode": 400, "body": json.dumps({"error": "Invalid JSON body"})}

    ocr_result = OcrResult(
        request_id=body.get("requestId", ""),
        status=body.get("status", ""),
        extracted_info=body.get("extractedInfo") or {},
        raw_response=body,
    )

    if not ocr_result.request_id:
        return {"statusCode": 400, "body": json.dumps({"error": "Missing requestId"})}

    use_case = build_handle_ocr_callback_use_case(settings, use_s3=True)
    try:
        scan_record = use_case.execute(ocr_result)
        return {
            "statusCode": 200,
            "body": json.dumps({"scan_id": scan_record.scan_id, "state": scan_record.state}),
        }
    except ValueError as exc:
        log.warning("ocr_webhook_no_record", error=str(exc))
        return {"statusCode": 404, "body": json.dumps({"error": str(exc)})}
    except Exception as exc:
        log.exception("ocr_webhook_handler_error", error=str(exc))
        return {"statusCode": 500, "body": json.dumps({"error": "Internal error"})}
