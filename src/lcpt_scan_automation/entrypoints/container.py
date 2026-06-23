"""Dependency injection wiring.

Build the concrete dependency graph from Settings.
Imported by both the CLI and the Lambda handler to keep DI centralised.
"""

from __future__ import annotations

from ..application.audit_note_builder import AuditNoteBuilder
from ..application.checklist_mapper import ChecklistMapper
from ..application.handle_ocr_callback import HandleOcrCallbackUseCase
from ..application.process_scan import ProcessScanUseCase
from ..config.settings import Settings
from ..infrastructure.idempotency.memory_idempotency_store import MemoryIdempotencyStore
from ..infrastructure.ocr.haulsafe_client import HaulSafeOcrClient
from ..infrastructure.pdf.pypdf_processor import PypdfProcessor
from ..infrastructure.review_queue.sharepoint_review_queue import SharePointReviewQueue
from ..infrastructure.storage.s3_storage import S3Storage


def build_process_scan_use_case(
    settings: Settings,
    *,
    s3_client=None,
) -> ProcessScanUseCase:
    storage = S3Storage(
        bucket=settings.lcpt_scan_bucket,
        region=settings.aws_region,
        presigned_url_expiry_seconds=settings.s3_presigned_url_expiry_seconds,
        aws_access_key_id=settings.aws_access_key_id or None,
        aws_secret_access_key=settings.aws_secret_access_key or None,
        client=s3_client,
    )

    ocr_client = HaulSafeOcrClient(
        base_url=settings.haul_ocr_base_url,
        api_key=settings.haul_ocr_api_key,
    )

    cp_client = _build_real_cp_client(settings)

    # Dedup memory: always use in-memory store (S3IdempotencyStore requires
    # s3:PutObject on state/scans/ which the IAM user may not have).
    idempotency_store = MemoryIdempotencyStore()
    review_queue = SharePointReviewQueue(
        tenant_id=settings.graph_tenant_id,
        client_id=settings.graph_client_id,
        client_secret=settings.graph_client_secret,
        site_id=settings.sharepoint_site_id,
        drive_id=settings.sharepoint_drive_id,
        storage=storage,
        timeout_seconds=settings.sharepoint_timeout_seconds,
    )
    checklist_mapper = ChecklistMapper(settings.checklist_mapping_path)
    audit_note_builder = AuditNoteBuilder()

    return ProcessScanUseCase(
        storage=storage,
        pdf_processor=PypdfProcessor(),
        ocr_client=ocr_client,
        cp_suite=cp_client,
        idempotency_store=idempotency_store,
        review_queue=review_queue,
        checklist_mapper=checklist_mapper,
        audit_note_builder=audit_note_builder,
        settings=settings,
    )


def build_handle_ocr_callback_use_case(
    settings: Settings,
    *,
    s3_client=None,
) -> HandleOcrCallbackUseCase:
    use_case = build_process_scan_use_case(
        settings,
        s3_client=s3_client,
    )
    return HandleOcrCallbackUseCase(process_scan=use_case)


def _build_real_cp_client(settings: Settings):
    from ..infrastructure.cp_suite.auth import CpSuiteTokenProvider
    from ..infrastructure.cp_suite.cp_suite_http_client import CpSuiteHttpClient

    token_provider = CpSuiteTokenProvider(
        identity_server=settings.cp_suite_identity_server,
        client_id=settings.cp_suite_client_id,
        client_secret=settings.cp_suite_client_secret,
        username=settings.cp_suite_username,
        password=settings.cp_suite_password,
        grant_type=settings.cp_suite_grant_type,
        refresh_margin_seconds=settings.cp_suite_token_refresh_margin_seconds,
        timeout_seconds=settings.cp_suite_timeout_seconds,
    )
    return CpSuiteHttpClient(
        base_url=settings.cp_suite_base_url,
        token_provider=token_provider,
        file_category=settings.cp_suite_file_category,
        user_id=settings.cp_suite_user_id,
        timeout_seconds=settings.cp_suite_timeout_seconds,
        note_tab_type_id=settings.cp_suite_note_tab_type_id,
    )
