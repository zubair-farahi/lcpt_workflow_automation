"""CLI for LCPT Scan Automation.

Commands:
    process-s3       — full production pipeline from an S3 object
    list-s3          — list objects in the S3 bucket (optionally filter by prefix)
    check-s3-access  — diagnose S3 bucket access and IAM permissions
    submit-ocr       — submit a document URL to the real HaulSafe OCR API
    get-ocr-result   — fetch an OCR result by request ID
    test-ocr         — submit + poll until complete, print normalized cover sheet
    cp-suite-token   — fetch a CP Suite bearer token (verifies auth works)
    cp-suite-get-wr  — fetch a Work Request (and optionally its tasks/items)
    callback         — feed a saved OCR JSON payload into the pipeline (debug only)

Examples:
    lcpt-scan process-s3 --key incoming/scan.pdf
    lcpt-scan list-s3 --prefix incoming/
    lcpt-scan check-s3-access
    lcpt-scan test-ocr --document-url "https://..."
    lcpt-scan cp-suite-get-wr --wr-number PFG-WR-351 --show-tasks
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Annotated, Optional

import typer

app = typer.Typer(help="LCPT Scan Automation CLI")

OCR_API_KEY_MISSING_MESSAGE = "Error: HAUL_OCR_API_KEY is not set."


def _get_settings():
    from ..config.settings import Settings, configure_logging

    s = Settings()
    configure_logging(s)
    return s


# ── process-s3 ────────────────────────────────────────────────────────────────

@app.command(name="process-s3")
def process_s3(
    bucket: Annotated[str, typer.Option(help="S3 bucket name")] = "fw-ocr-project",
    key: Annotated[str, typer.Option(help="S3 object key (e.g. incoming/scan.pdf)")] = ...,
) -> None:
    """Process a scan from S3 end-to-end with production adapters."""
    from ..domain.models import ScanEvent
    from ..infrastructure.storage.s3_storage import S3Storage
    from .container import build_process_scan_use_case

    settings = _get_settings()

    s3 = S3Storage(
        bucket=bucket,
        region=settings.aws_region,
        presigned_url_expiry_seconds=settings.s3_presigned_url_expiry_seconds,
        aws_access_key_id=settings.aws_access_key_id or None,
        aws_secret_access_key=settings.aws_secret_access_key or None,
    )

    typer.echo("Mode: production end-to-end")
    typer.echo(f"Fetching metadata: s3://{bucket}/{key} ...")
    try:
        metadata = s3.get_object_metadata(key)
    except Exception as exc:
        typer.echo(f"Error fetching S3 metadata: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo(f"  ETag: {metadata.etag}  Size: {metadata.size} bytes")

    event = ScanEvent(source_path=key, etag=metadata.etag)
    # The s3-ocr IAM user only has PutObject/DeleteObject on test-uploads/.
    # Override the processing prefix so the temp cover PNG is written there.
    # In Lambda/production, the IAM role has access to processing/ normally.
    settings_s3 = settings.model_copy(update={
        "lcpt_scan_bucket": bucket,
        "lcpt_scan_processing_prefix": "test-uploads/",
    })
    use_case = build_process_scan_use_case(
        settings_s3,
        s3_client=s3._client,
    )
    typer.echo(f"Running pipeline for s3://{bucket}/{key} ...")
    try:
        record = use_case.execute(event)
    except Exception as exc:
        typer.echo(f"Pipeline error: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo(json.dumps({"scan_id": record.scan_id, "state": record.state}, indent=2))


# ── list-s3 ───────────────────────────────────────────────────────────────────

@app.command(name="list-s3")
def list_s3(
    bucket: Annotated[Optional[str], typer.Option(help="Override bucket name")] = None,
    prefix: Annotated[str, typer.Option(help="Filter by prefix, e.g. incoming/")] = "",
    max_keys: Annotated[int, typer.Option(help="Max results to return")] = 200,
) -> None:
    """List objects in the S3 bucket. Shows key, size, and last-modified date."""
    from ..infrastructure.storage.s3_storage import S3Storage

    settings = _get_settings()
    effective_bucket = bucket or settings.lcpt_scan_bucket

    storage = S3Storage(
        bucket=effective_bucket,
        region=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id or None,
        aws_secret_access_key=settings.aws_secret_access_key or None,
    )

    try:
        objects = storage.list_objects(prefix=prefix, max_keys=max_keys)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    if not objects:
        msg = f"s3://{effective_bucket}/{prefix}" if prefix else f"s3://{effective_bucket}/"
        typer.echo(f"No objects found under {msg}")
        return

    typer.echo(f"\n{'KEY':<60} {'SIZE':>10}  {'LAST MODIFIED'}")
    typer.echo("-" * 90)
    for obj in objects:
        size_str = f"{obj.size:,}" if obj.size is not None else "?"
        date_str = obj.last_modified.strftime("%Y-%m-%d %H:%M") if obj.last_modified else ""
        typer.echo(f"{obj.key:<60} {size_str:>10}  {date_str}")

    typer.echo(f"\n{len(objects)} object(s) in s3://{effective_bucket}/{prefix}")


# ── check-s3-access ───────────────────────────────────────────────────────────

@app.command(name="check-s3-access")
def check_s3_access(
    bucket: Annotated[Optional[str], typer.Option(help="Override bucket name")] = None,
    test_write: Annotated[bool, typer.Option(help="Test PutObject/GetObject/DeleteObject")] = True,
    prefix: Annotated[str, typer.Option(help="Prefix to list and write under")] = "diagnostics/",
) -> None:
    """Diagnose S3 bucket access and report which IAM permissions work."""
    from ..infrastructure.storage.s3_storage import S3Storage

    settings = _get_settings()
    effective_bucket = bucket or settings.lcpt_scan_bucket

    typer.echo(f"\nChecking S3 access for bucket: {effective_bucket}")
    typer.echo(f"Region: {settings.aws_region}\n")

    # 1. STS caller identity
    try:
        sts = settings.build_sts_client()
        identity = sts.get_caller_identity()
        typer.echo(f"STS Identity       ✓  ARN: {identity.get('Arn', 'unknown')}")
        typer.echo(f"                      Account: {identity.get('Account', 'unknown')}")
    except Exception as exc:
        typer.echo(f"STS Identity       ✗  {exc}", err=True)

    storage = S3Storage(
        bucket=effective_bucket,
        region=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id or None,
        aws_secret_access_key=settings.aws_secret_access_key or None,
    )

    # 2. HeadBucket
    ok, msg = storage.diag_head_bucket()
    typer.echo(msg, err=not ok)

    # 3. ListObjects
    ok, msg = storage.diag_list_objects(prefix="")
    typer.echo(msg, err=not ok)

    # 4. List incoming prefix
    ok, msg = storage.diag_list_objects(prefix=settings.lcpt_scan_incoming_prefix)
    typer.echo(msg, err=not ok)

    if test_write:
        # 5. PutObject
        ok, msg, test_key = storage.diag_put_object(prefix=prefix)
        typer.echo(msg, err=not ok)

        if test_key:
            # 6. GetObject
            ok, msg = storage.diag_get_object(test_key)
            typer.echo(msg, err=not ok)

            # 7. DeleteObject
            ok, msg = storage.diag_delete_object(test_key)
            typer.echo(msg, err=not ok)

    typer.echo("\nDone. Lines marked ✗ indicate missing permissions or misconfigurations.")


# ── callback ──────────────────────────────────────────────────────────────────

@app.command()
def callback(
    request_id: Annotated[str, typer.Option(help="OCR request ID")] = ...,
    payload: Annotated[
        Optional[Path], typer.Option(help="Path to JSON file with OCR result")
    ] = None,
) -> None:
    """Simulate receiving an OCR callback result and continue the pipeline."""
    from ..domain.models import OcrResult
    from .container import build_handle_ocr_callback_use_case

    settings = _get_settings()

    extracted_info: dict = {}
    status = "COMPLETED"
    if payload:
        data = json.loads(payload.read_text(encoding="utf-8"))
        extracted_info = data.get("extractedInfo", {})
        status = data.get("status", "COMPLETED")

    ocr_result = OcrResult(
        request_id=request_id,
        status=status,
        extracted_info=extracted_info,
    )

    use_case = build_handle_ocr_callback_use_case(settings)
    try:
        record = use_case.execute(ocr_result)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo(json.dumps({"scan_id": record.scan_id, "state": record.state}, indent=2))


# ── cp-suite-token ────────────────────────────────────────────────────────────

@app.command(name="cp-suite-token")
def cp_suite_token() -> None:
    """Fetch a CP Suite bearer token to verify auth works. The token is NOT printed."""
    settings = _get_settings()
    from ..infrastructure.cp_suite.auth import CpSuiteTokenProvider

    provider = CpSuiteTokenProvider(
        identity_server=settings.cp_suite_identity_server,
        client_id=settings.cp_suite_client_id,
        client_secret=settings.cp_suite_client_secret,
        username=settings.cp_suite_username,
        password=settings.cp_suite_password,
        grant_type=settings.cp_suite_grant_type,
    )
    try:
        token = provider.get_token()
    except Exception as exc:
        typer.echo(f"Token fetch failed: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Token acquired OK (length={len(token)} chars). Not displaying the token value.")


# ── cp-suite-get-wr ───────────────────────────────────────────────────────────

@app.command(name="cp-suite-get-wr")
def cp_suite_get_wr(
    wr_number: Annotated[str, typer.Option(help="Work Request display ID, e.g. PFG-WR-351")] = ...,
    show_tasks: Annotated[bool, typer.Option(help="Also fetch tasks and checklist items")] = False,
) -> None:
    """Fetch a Work Request from CP Suite (and optionally its tasks/checklist items)."""
    settings = _get_settings()
    from .container import _build_real_cp_client

    client = _build_real_cp_client(settings)
    try:
        wr = client.get_work_request(wr_number)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo(json.dumps(
        {
            "work_request_id": wr.work_request_id,
            "work_request_number": wr.work_request_number,
            "title": wr.title,
            "location_id": wr.location_id,
            "client_root_location_id": wr.client_root_location_id,
        },
        indent=2,
    ))

    if show_tasks:
        try:
            tasks = client.get_tasks(wr.work_request_id)
        except Exception as exc:
            typer.echo(f"Error fetching tasks: {exc}", err=True)
            raise typer.Exit(1)

        for task in tasks:
            typer.echo(f"\nTask {task.task_id}  type='{task.task_type}'  name='{task.name}'")
            try:
                items = client.get_checklist_items(task.task_id)
            except Exception as exc:
                typer.echo(f"  (failed to fetch checklist items: {exc})", err=True)
                continue
            for item in items:
                mark = "x" if item.is_complete else " "
                typer.echo(f"  [{mark}] {item.name}  ({item.item_id})")


# ── submit-ocr ────────────────────────────────────────────────────────────────

@app.command(name="submit-ocr")
def submit_ocr(
    document_url: Annotated[str, typer.Option(help="Publicly accessible document URL")] = ...,
    fields_config: Annotated[
        Optional[Path], typer.Option(help="YAML file with OCR field definitions")
    ] = None,
) -> None:
    """Submit a document URL to the real HaulSafe OCR API and print the request ID."""
    settings = _get_settings()
    from ..infrastructure.ocr.haulsafe_client import HaulSafeOcrClient

    if not settings.haul_ocr_api_key:
        typer.echo(OCR_API_KEY_MISSING_MESSAGE, err=True)
        raise typer.Exit(1)

    client = HaulSafeOcrClient(
        base_url=settings.haul_ocr_base_url,
        api_key=settings.haul_ocr_api_key,
    )

    if fields_config:
        import yaml
        from ..domain.models import OcrField

        raw = yaml.safe_load(fields_config.read_text(encoding="utf-8"))
        fields = [OcrField(field_name=f["fieldName"], field_type=f["fieldType"]) for f in raw["fields"]]
    else:
        fields = settings.load_ocr_fields()

    result = client.submit_document(document_url, fields)
    typer.echo(json.dumps({"request_id": result.request_id, "status": result.status}, indent=2))


# ── get-ocr-result ────────────────────────────────────────────────────────────

@app.command(name="get-ocr-result")
def get_ocr_result(
    request_id: Annotated[str, typer.Option(help="OCR request ID to fetch")] = ...,
) -> None:
    """Fetch and print the current OCR result for a request ID."""
    settings = _get_settings()
    from ..infrastructure.ocr.haulsafe_client import HaulSafeOcrClient

    if not settings.haul_ocr_api_key:
        typer.echo(OCR_API_KEY_MISSING_MESSAGE, err=True)
        raise typer.Exit(1)

    client = HaulSafeOcrClient(
        base_url=settings.haul_ocr_base_url,
        api_key=settings.haul_ocr_api_key,
    )
    result = client.get_result(request_id)
    typer.echo(
        json.dumps(
            {
                "request_id": result.request_id,
                "status": result.status,
                "extracted_info": result.extracted_info,
            },
            indent=2,
            default=str,
        )
    )


# ── test-ocr ──────────────────────────────────────────────────────────────────

@app.command(name="test-ocr")
def test_ocr(
    document_url: Annotated[str, typer.Option(help="Publicly accessible document URL")] = ...,
) -> None:
    """Submit a document to HaulSafe OCR, poll until complete, print normalized result.

    The API key is never printed.
    """
    settings = _get_settings()
    from ..infrastructure.ocr.haulsafe_client import HaulSafeOcrClient

    if not settings.haul_ocr_api_key:
        typer.echo(OCR_API_KEY_MISSING_MESSAGE, err=True)
        raise typer.Exit(1)

    client = HaulSafeOcrClient(
        base_url=settings.haul_ocr_base_url,
        api_key=settings.haul_ocr_api_key,
    )

    fields = settings.load_ocr_fields()
    typer.echo("Submitting document for OCR ...")
    submission = client.submit_document(document_url, fields)
    typer.echo(f"Request ID: {submission.request_id}  Status: {submission.status}")

    interval = settings.haul_ocr_poll_interval_seconds
    max_attempts = settings.haul_ocr_max_attempts

    for attempt in range(1, max_attempts + 1):
        result = client.get_result(submission.request_id)
        typer.echo(f"Poll {attempt}/{max_attempts}: status={result.status}")

        if result.status == "COMPLETED":
            from ..application.ocr_result_parser import parse_ocr_extracted_info

            cover_sheet, routing_reason = parse_ocr_extracted_info(result.extracted_info)
            typer.echo("\n── Extracted cover sheet ──────────────────────────")
            typer.echo(json.dumps(cover_sheet.model_dump(mode="json"), indent=2, default=str))
            if routing_reason:
                typer.echo(f"\nRouting issue: {routing_reason}", err=True)
            return

        if result.status == "FAILED":
            typer.echo("OCR returned FAILED.", err=True)
            raise typer.Exit(1)

        if attempt < max_attempts:
            time.sleep(interval)

    typer.echo(f"Timed out after {max_attempts} polls.", err=True)
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
