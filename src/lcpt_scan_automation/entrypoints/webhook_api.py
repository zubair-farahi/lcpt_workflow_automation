"""FastAPI app exposing a local OCR callback webhook endpoint.

Start with:
    uvicorn lcpt_scan_automation.entrypoints.webhook_api:app --reload

Endpoint:
    POST /lcpt-scans/ocr-callback
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ..config.settings import Settings, configure_logging
from ..domain.models import OcrResult
from .container import build_handle_ocr_callback_use_case

app = FastAPI(title="LCPT Scan Automation — Local Webhook")
log = structlog.get_logger()

_settings = Settings()
configure_logging(_settings)


class OcrCallbackPayload(BaseModel):
    requestId: str
    status: str
    extractedInfo: dict[str, Any] = {}


@app.post("/lcpt-scans/ocr-callback")
def ocr_callback(payload: OcrCallbackPayload) -> dict[str, Any]:
    """Receive an OCR result and continue the scan pipeline."""
    ocr_result = OcrResult(
        request_id=payload.requestId,
        status=payload.status,
        extracted_info=payload.extractedInfo,
        raw_response=payload.model_dump(),
    )

    use_case = build_handle_ocr_callback_use_case(_settings)
    try:
        record = use_case.execute(ocr_result)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.exception("webhook_handler_error", error=str(exc))
        raise HTTPException(status_code=500, detail="Internal error")

    return {"scan_id": record.scan_id, "state": record.state}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
