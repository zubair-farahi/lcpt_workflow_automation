"""AWS Lambda handler for LCPT Scan Automation.

Two handler functions:
  handle_s3_event    — triggered by S3 ObjectCreated, runs the full pipeline.
  handle_ocr_webhook — triggered by API Gateway (future webhook from HaulSafe).

Business logic lives in ProcessScanUseCase / HandleOcrCallbackUseCase.
This file only parses the event, wires dependencies, and formats the response.
"""

from __future__ import annotations

import json
import os
from typing import Any

import structlog

from ..config.settings import Settings, configure_logging
from ..domain.models import OcrResult, ScanEvent
from .container import build_handle_ocr_callback_use_case, build_process_scan_use_case

log = structlog.get_logger()

_secrets_loaded = False


def _load_secrets_into_env() -> None:
    """Fetch runtime secrets from AWS Secrets Manager into the environment.

    If LCPT_SECRETS_ARN is set, the secret's JSON payload (e.g.
    {"HAUL_OCR_API_KEY": "...", "CP_SUITE_PASSWORD": "..."}) is loaded into
    os.environ BEFORE Settings() is constructed. Existing env vars win, so
    individual values can still be overridden per-function. Cached for the
    lifetime of the Lambda container (secrets are re-read on cold start).
    """
    global _secrets_loaded
    if _secrets_loaded:
        return
    arn = os.environ.get("LCPT_SECRETS_ARN", "").strip()
    if arn:
        import boto3

        client = boto3.client("secretsmanager")
        payload = client.get_secret_value(SecretId=arn)["SecretString"]
        injected = 0
        for key, value in json.loads(payload).items():
            if key.upper() not in os.environ:
                os.environ[key.upper()] = str(value)
                injected += 1
        log.info("secrets_loaded_from_aws", keys_injected=injected)
    _secrets_loaded = True


def handle_s3_event(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entry point for S3 ObjectCreated events (direct or via SQS)."""
    _load_secrets_into_env()
    settings = Settings()
    configure_logging(settings)

    results = []
    for record in _unwrap_records(event):
        s3_info = record.get("s3", {})
        object_key = s3_info.get("object", {}).get("key", "")
        etag = s3_info.get("object", {}).get("eTag", "")

        if not object_key:
            log.warning("s3_event_missing_key", record=record)
            continue

        scan_event = ScanEvent(source_path=object_key, etag=etag)
        use_case = build_process_scan_use_case(settings)

        try:
            scan_record = use_case.execute(scan_event)
            results.append({"scan_id": scan_record.scan_id, "state": scan_record.state})
        except Exception as exc:
            log.exception("lambda_s3_handler_error", key=object_key, error=str(exc))
            results.append({"key": object_key, "error": str(exc)})

    return {"statusCode": 200, "body": json.dumps({"processed": results})}


def _unwrap_records(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Yield S3 event records whether the event came direct from S3 or via SQS.

    SQS wraps each S3 event as a message whose body is the S3 event JSON.
    """
    records: list[dict[str, Any]] = []
    for record in event.get("Records", []):
        if "s3" in record:
            records.append(record)
        elif record.get("eventSource") == "aws:sqs":
            try:
                inner = json.loads(record.get("body") or "{}")
            except json.JSONDecodeError:
                log.warning("sqs_body_not_json", message_id=record.get("messageId"))
                continue
            records.extend(r for r in inner.get("Records", []) if "s3" in r)
    return records


def handle_ocr_webhook(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entry point for HaulSafe OCR webhook callbacks (future async mode).

    Expects the raw HaulSafe callback JSON in event["body"].
    """
    _load_secrets_into_env()
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

    use_case = build_handle_ocr_callback_use_case(settings)
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
