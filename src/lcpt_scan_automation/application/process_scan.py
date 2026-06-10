"""ProcessScanUseCase — the main orchestration use case for AIP-388."""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Optional

import structlog
import structlog.contextvars

from ..domain.enums import OcrStatus, ProcessingState, ReviewReasonCode, RoutingType
from ..domain.errors import (
    CpSuiteError,
    CpSuiteNotFoundError,
    OcrError,
    OcrTimeoutError,
    PdfProcessingError,
    StorageError,
)
from ..domain.models import CoverSheet, OcrResult, ReviewItem, ScanEvent, ScanRecord
from .audit_note_builder import AuditNoteBuilder
from .checklist_mapper import ChecklistMapper
from .ocr_result_parser import parse_ocr_extracted_info

if TYPE_CHECKING:
    from ..config.settings import Settings
    from ..ports.cp_suite_client import CpSuiteClientPort
    from ..ports.idempotency_store import IdempotencyStorePort
    from ..ports.ocr_client import OcrClientPort
    from ..ports.pdf_processor import PdfProcessorPort
    from ..ports.review_queue import ReviewQueuePort
    from ..ports.storage import StoragePort

log = structlog.get_logger()


class ProcessScanUseCase:
    """Orchestrates the full scan pipeline from PDF ingestion to CP Suite update.

    Two entry points:
      execute()                — full flow: read PDF → OCR (with polling) → CP Suite
      continue_from_ocr_result() — starts after OCR is already complete (future webhook path)
    """

    def __init__(
        self,
        storage: StoragePort,
        pdf_processor: PdfProcessorPort,
        ocr_client: OcrClientPort,
        cp_suite: CpSuiteClientPort,
        idempotency_store: IdempotencyStorePort,
        review_queue: ReviewQueuePort,
        checklist_mapper: ChecklistMapper,
        audit_note_builder: AuditNoteBuilder,
        settings: Settings,
    ) -> None:
        self._storage = storage
        self._pdf = pdf_processor
        self._ocr = ocr_client
        self._cp = cp_suite
        self._idempotency = idempotency_store
        self._review_queue = review_queue
        self._checklist_mapper = checklist_mapper
        self._audit_note = audit_note_builder
        self._settings = settings

    # ── Public entry points ────────────────────────────────────────────────────

    def execute(self, event: ScanEvent) -> ScanRecord:
        """Process a scan from scratch: read PDF, submit to OCR, poll, update CP Suite."""
        scan_id = event.scan_id or str(uuid.uuid4())
        correlation_id = event.correlation_id or scan_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(scan_id=scan_id, correlation_id=correlation_id)
        log.info("scan_received", source=event.source_path)

        # ── Idempotency check ──────────────────────────────────────────────────
        existing = self._idempotency.find_by_source(event.source_path, event.etag)
        if existing:
            if existing.state == ProcessingState.SUCCESS:
                log.info("scan_already_succeeded", existing_id=existing.scan_id)
                return existing
            if existing.state not in (ProcessingState.FAILED, ProcessingState.REVIEW_REQUIRED):
                log.info("scan_duplicate_skipped", state=existing.state, existing_id=existing.scan_id)
                return existing
            # FAILED or REVIEW_REQUIRED → allow retry, reuse same scan_id
            log.info("scan_retrying", state=existing.state, existing_id=existing.scan_id)
            scan_id = existing.scan_id

        record = ScanRecord(
            scan_id=scan_id,
            source_path=event.source_path,
            source_etag=event.etag,
            state=ProcessingState.RECEIVED,
            correlation_id=correlation_id,
        )
        self._idempotency.save(record)

        try:
            return self._run_pipeline(record)
        except Exception as exc:
            log.exception("scan_pipeline_unexpected_error", error=str(exc))
            self._idempotency.update_state(record.scan_id, ProcessingState.FAILED, {"error": str(exc)})
            raise

    def continue_from_ocr_result(self, ocr_result: OcrResult) -> ScanRecord:
        """Continue pipeline from a completed OCR result (webhook or manual trigger).

        Looks up the in-flight scan record by ocr_request_id.
        """
        record = self._idempotency.find_by_ocr_request_id(ocr_result.request_id)
        if record is None:
            log.warning("ocr_callback_no_matching_record", request_id=ocr_result.request_id)
            raise ValueError(f"No scan record found for OCR request {ocr_result.request_id}")

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            scan_id=record.scan_id,
            correlation_id=record.correlation_id,
        )
        log.info("ocr_callback_received", request_id=ocr_result.request_id, status=ocr_result.status)

        packet_key = f"processing/{record.scan_id}/packet.pdf"
        try:
            packet_bytes = self._storage.read_bytes(packet_key)
        except StorageError as exc:
            return self._to_review(record, ReviewReasonCode.UNEXPECTED_ERROR, str(exc), None)

        return self._process_from_ocr(record, ocr_result, packet_bytes)

    # ── Private pipeline steps ─────────────────────────────────────────────────

    def _run_pipeline(self, record: ScanRecord) -> ScanRecord:
        # 1. Read PDF
        try:
            pdf_bytes = self._storage.read_bytes(record.source_path)
        except StorageError as exc:
            return self._to_review(record, ReviewReasonCode.UNEXPECTED_ERROR, str(exc), None)

        # 2. Split PDF
        try:
            split = self._pdf.split_cover_and_packet(pdf_bytes)
        except PdfProcessingError as exc:
            return self._to_review(record, ReviewReasonCode.UNEXPECTED_ERROR, str(exc), None)

        if split.total_pages == 1:
            return self._to_review(
                record,
                ReviewReasonCode.SINGLE_PAGE_PDF,
                "PDF has only 1 page — no packet content present.",
                None,
            )

        log.info("pdf_split", total_pages=split.total_pages)

        # 3. Render the cover sheet to PNG and store alongside the packet.
        #    HaulSafe's vision AI 500s on raw PDFs, so OCR gets an image.
        #    Render from the ORIGINAL bytes (page 0): splitting via pypdf
        #    strips the /AcroForm dict, which would blank out filled form
        #    fields in the rendered image.
        try:
            cover_png_bytes = self._pdf.render_pdf_to_png(pdf_bytes)
        except PdfProcessingError as exc:
            return self._to_review(record, ReviewReasonCode.UNEXPECTED_ERROR, str(exc), None)

        cover_key = f"processing/{record.scan_id}/cover_sheet.png"
        packet_key = f"processing/{record.scan_id}/packet.pdf"
        self._storage.write_bytes(cover_key, cover_png_bytes)
        self._storage.write_bytes(packet_key, split.packet_bytes)

        # 4. Get accessible URL for cover sheet page (OCR requirement)
        try:
            cover_url = self._storage.generate_accessible_url(cover_key)
        except StorageError as exc:
            return self._to_review(record, ReviewReasonCode.UNEXPECTED_ERROR, str(exc), None)

        # 5. Submit to OCR
        ocr_fields = self._settings.load_ocr_fields()
        try:
            submission = self._ocr.submit_document(cover_url, ocr_fields)
        except OcrError as exc:
            return self._to_review(record, ReviewReasonCode.OCR_FAILED, str(exc), None)

        log.info("ocr_submitted", request_id=submission.request_id)
        record.ocr_request_id = submission.request_id
        self._idempotency.update_state(
            record.scan_id,
            ProcessingState.AWAITING_OCR,
            {"ocr_request_id": submission.request_id},
        )
        record.state = ProcessingState.AWAITING_OCR

        # 6. Poll for OCR result
        try:
            ocr_result = self._poll_ocr(submission.request_id)
        except (OcrTimeoutError, OcrError) as exc:
            return self._to_review(
                record, ReviewReasonCode.OCR_FAILED_OR_UNKNOWN_STATUS, str(exc), None
            )

        return self._process_from_ocr(record, ocr_result, split.packet_bytes)

    def _poll_ocr(self, request_id: str) -> OcrResult:
        interval = self._settings.haul_ocr_poll_interval_seconds
        max_attempts = self._settings.haul_ocr_max_attempts

        for attempt in range(1, max_attempts + 1):
            result = self._ocr.get_result(request_id)
            log.info("ocr_poll", request_id=request_id, status=result.status, attempt=attempt)

            if result.status == OcrStatus.COMPLETED:
                return result

            try:
                status_enum = OcrStatus(result.status)
            except ValueError:
                log.warning("ocr_unknown_status", status=result.status, request_id=request_id)
                raise OcrError(f"Unknown OCR status '{result.status}' for request {request_id}")

            if status_enum == OcrStatus.FAILED:
                raise OcrError(f"OCR reported FAILED for request {request_id}")

            # QUEUED or PROCESSING — keep waiting
            if attempt < max_attempts:
                time.sleep(interval)

        raise OcrTimeoutError(
            f"OCR polling timed out after {max_attempts} attempts (request {request_id})"
        )

    def _process_from_ocr(
        self,
        record: ScanRecord,
        ocr_result: OcrResult,
        packet_bytes: bytes,
    ) -> ScanRecord:
        """Continue pipeline from a completed OCR result through to CP Suite."""
        self._idempotency.update_state(record.scan_id, ProcessingState.OCR_RECEIVED)

        # 7. Parse OCR result into domain model
        cover_sheet, routing_reason = parse_ocr_extracted_info(ocr_result.extracted_info)
        cover_sheet = cover_sheet.model_copy(update={"raw_ocr": ocr_result.extracted_info})

        if routing_reason:
            return self._to_review(
                record, routing_reason, f"Routing validation failed: {routing_reason}", cover_sheet
            )

        # 8. Validate cover sheet fields
        validation_reason = self._validate_cover_sheet(cover_sheet)
        if validation_reason:
            self._idempotency.update_state(record.scan_id, ProcessingState.VALIDATION_FAILED)
            return self._to_review(
                record,
                validation_reason,
                f"Cover sheet validation failed: {validation_reason}",
                cover_sheet,
            )

        self._idempotency.update_state(
            record.scan_id,
            ProcessingState.CP_PROCESSING,
            {"work_request_number": cover_sheet.work_request_number},
        )
        record.state = ProcessingState.CP_PROCESSING

        return self._process_cp_suite(
            record,
            cover_sheet,
            packet_bytes,
            ocr_result.request_id,
        )

    def _validate_cover_sheet(self, cover_sheet: CoverSheet) -> Optional[ReviewReasonCode]:
        import re

        from ..domain.validation import (
            validate_company_name,
            validate_company_prefix,
            validate_confidence,
            validate_wr_number,
        )

        reason = validate_company_name(cover_sheet.company_name)
        if reason:
            return reason

        pattern = re.compile(self._settings.wr_number_pattern)
        reason = validate_wr_number(cover_sheet.work_request_number, pattern)
        if reason:
            return reason

        reason = validate_confidence(
            cover_sheet.field_confidences,
            required_fields=["companyName", "workRequestNumber"],
            threshold=self._settings.ocr_confidence_threshold,
            require_confidence=self._settings.require_ocr_confidence,
        )
        if reason:
            return reason

        prefix_mapping = self._settings.load_company_prefix_mapping()
        return validate_company_prefix(
            cover_sheet.company_name or "",
            cover_sheet.work_request_number or "",
            prefix_mapping,
            self._settings.company_prefix_validation_required,
        )

    def _process_cp_suite(
        self,
        record: ScanRecord,
        cover_sheet: CoverSheet,
        packet_bytes: bytes,
        ocr_request_id: str,
    ) -> ScanRecord:
        wr_number = cover_sheet.work_request_number or ""
        routing = cover_sheet.routing

        # Fetch Work Request
        try:
            work_request = self._cp.get_work_request(wr_number)
        except CpSuiteNotFoundError:
            return self._to_review(
                record,
                ReviewReasonCode.WORK_REQUEST_NOT_FOUND,
                f"Work Request '{wr_number}' not found in CP Suite.",
                cover_sheet,
            )
        except CpSuiteError as exc:
            return self._to_review(record, ReviewReasonCode.CP_SUITE_ERROR, str(exc), cover_sheet)

        log.info("cp_work_request_found", wr_id=work_request.work_request_id, wr_number=wr_number)

        # Attach packet PDF
        packet_filename = f"{wr_number}_packet.pdf"
        try:
            if routing == RoutingType.INTERNAL:
                self._cp.attach_pdf_internal(work_request, packet_bytes, packet_filename)
            else:
                self._cp.attach_pdf_external(work_request, packet_bytes, packet_filename)
            log.info("cp_attachment_added", routing=routing, wr_id=work_request.work_request_id)
        except CpSuiteError as exc:
            return self._to_review(record, ReviewReasonCode.CP_SUITE_ERROR, str(exc), cover_sheet)

        # Update checklist items across all tasks
        result = self._update_checklists(record, cover_sheet, work_request.work_request_id)
        if result is not None:
            return result  # routed to review inside _update_checklists

        # Write audit note
        note = self._audit_note.build(
            record, cover_sheet, routing, cover_sheet.checked_actions, ocr_request_id
        )
        try:
            self._cp.add_system_note(work_request.work_request_id, note)
            log.info("audit_note_written", wr_id=work_request.work_request_id)
        except CpSuiteError as exc:
            # Non-fatal: note failure should not block the scan from succeeding
            log.warning("audit_note_failed", error=str(exc))

        self._idempotency.update_state(
            record.scan_id,
            ProcessingState.SUCCESS,
            {
                "work_request_id": work_request.work_request_id,
                "work_request_number": wr_number,
            },
        )
        record.state = ProcessingState.SUCCESS
        log.info("scan_success", wr_number=wr_number)
        return record

    def _update_checklists(
        self,
        record: ScanRecord,
        cover_sheet: CoverSheet,
        wr_id: str,
    ) -> Optional[ScanRecord]:
        """Mark checklist items complete for every task on the Work Request.

        Returns a ScanRecord routed to review if policy requires it, otherwise None.
        """
        try:
            tasks = self._cp.get_tasks(wr_id)
        except CpSuiteError as exc:
            return self._to_review(record, ReviewReasonCode.CP_SUITE_ERROR, str(exc), cover_sheet)

        policy = self._settings.missing_checklist_item_policy

        for task in tasks:
            try:
                checklist_items = self._cp.get_checklist_items(task.task_id)
            except CpSuiteError as exc:
                log.warning("cp_checklist_fetch_failed", task_id=task.task_id, error=str(exc))
                continue

            items_by_name = {item.name.lower(): item for item in checklist_items}

            for action in cover_sheet.checked_actions:
                expected_name = self._checklist_mapper.get_checklist_item_name(task.task_type, action)
                if expected_name is None:
                    log.debug("checklist_mapping_missing", task_type=task.task_type, action=action)
                    continue

                item = items_by_name.get(expected_name.lower())
                if item is None:
                    log.warning(
                        "checklist_item_not_found",
                        expected=expected_name,
                        task_id=task.task_id,
                        policy=policy,
                    )
                    if policy == "review":
                        return self._to_review(
                            record,
                            ReviewReasonCode.MISSING_CHECKLIST_ITEM,
                            f"Checklist item '{expected_name}' not found on task {task.task_id} "
                            f"(type={task.task_type}).",
                            cover_sheet,
                        )
                    continue  # policy == "skip"

                if item.is_complete:
                    log.debug("checklist_item_already_complete", item_id=item.item_id)
                    continue

                try:
                    self._cp.mark_checklist_item_complete(task.task_id, item.item_id)
                    log.info("checklist_item_marked_complete", item_name=item.name, task_id=task.task_id)
                except CpSuiteError as exc:
                    log.warning("checklist_item_mark_failed", item_id=item.item_id, error=str(exc))

        return None

    def _to_review(
        self,
        record: ScanRecord,
        reason: ReviewReasonCode,
        message: str,
        cover_sheet: Optional[CoverSheet],
    ) -> ScanRecord:
        self._idempotency.update_state(record.scan_id, ProcessingState.REVIEW_REQUIRED, {"reason": reason})
        review_item = ReviewItem(
            scan_id=record.scan_id,
            source_path=record.source_path,
            reason_code=reason,
            message=message,
            extracted_cover_sheet=cover_sheet,
            processing_state=ProcessingState.REVIEW_REQUIRED,
            correlation_id=record.correlation_id,
        )
        self._review_queue.enqueue(review_item)
        log.warning("scan_routed_to_review", reason=reason, message=message)
        record.state = ProcessingState.REVIEW_REQUIRED
        return record
