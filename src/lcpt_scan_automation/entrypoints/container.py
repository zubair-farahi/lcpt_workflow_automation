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
from ..infrastructure.cp_suite.mock_cp_suite_client import MockCpSuiteClient
from ..infrastructure.idempotency.local_idempotency_store import LocalIdempotencyStore
from ..infrastructure.ocr.haulsafe_client import HaulSafeOcrClient
from ..infrastructure.ocr.mock_ocr_client import MockOcrClient
from ..infrastructure.pdf.pypdf_processor import PypdfProcessor
from ..infrastructure.review_queue.local_review_queue import LocalReviewQueue
from ..infrastructure.storage.local_storage import LocalStorage
from ..infrastructure.storage.s3_storage import S3Storage


def build_process_scan_use_case(
    settings: Settings,
    *,
    use_mock_ocr: bool = False,
    use_mock_cp: bool = False,
    use_s3: bool = False,
    s3_client=None,
) -> ProcessScanUseCase:
    if use_s3:
        storage = S3Storage(
            bucket=settings.lcpt_scan_bucket,
            region=settings.aws_region,
            presigned_url_expiry_seconds=settings.s3_presigned_url_expiry_seconds,
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
            client=s3_client,
        )
    else:
        storage = LocalStorage(
            base_dir=settings.local_storage_dir,
            base_url=settings.local_storage_base_url or None,
        )

    ocr_client = (
        MockOcrClient()
        if use_mock_ocr
        else HaulSafeOcrClient(
            base_url=settings.haul_ocr_base_url,
            api_key=settings.haul_ocr_api_key,
        )
    )

    cp_client = (
        MockCpSuiteClient()
        if use_mock_cp
        else _build_real_cp_client(settings)
    )

    idempotency_store = LocalIdempotencyStore(
        db_path=f"{settings.local_storage_dir}/idempotency.db"
    )
    review_queue = LocalReviewQueue(queue_dir=settings.review_queue_dir)
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
    use_mock_cp: bool = False,
    use_s3: bool = False,
    s3_client=None,
) -> HandleOcrCallbackUseCase:
    # The callback path doesn't re-submit to OCR, so always use mock OCR
    use_case = build_process_scan_use_case(
        settings,
        use_mock_ocr=True,
        use_mock_cp=use_mock_cp,
        use_s3=use_s3,
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
