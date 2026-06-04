"""Shared fixtures for all tests."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
import yaml
from pypdf import PdfWriter

from lcpt_scan_automation.application.audit_note_builder import AuditNoteBuilder
from lcpt_scan_automation.application.checklist_mapper import ChecklistMapper
from lcpt_scan_automation.application.process_scan import ProcessScanUseCase
from lcpt_scan_automation.config.settings import Settings
from lcpt_scan_automation.infrastructure.cp_suite.mock_cp_suite_client import MockCpSuiteClient
from lcpt_scan_automation.infrastructure.idempotency.local_idempotency_store import LocalIdempotencyStore
from lcpt_scan_automation.infrastructure.ocr.mock_ocr_client import MockOcrClient
from lcpt_scan_automation.infrastructure.pdf.pypdf_processor import PypdfProcessor
from lcpt_scan_automation.infrastructure.review_queue.local_review_queue import LocalReviewQueue
from lcpt_scan_automation.infrastructure.storage.local_storage import LocalStorage


# ── PDF helpers ────────────────────────────────────────────────────────────────

def make_pdf(num_pages: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def two_page_pdf() -> bytes:
    return make_pdf(2)


@pytest.fixture
def single_page_pdf() -> bytes:
    return make_pdf(1)


# ── Settings ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_mapping_yaml(tmp_path: Path) -> Path:
    mapping = {
        "mappings": [
            {
                "task_type": "TYPE_A",
                "action": "PROCESS_THROUGH_STATE_AGENCY",
                "checklist_item_name": "Process through state agency",
            },
            {
                "task_type": "TYPE_A",
                "action": "RECEIVE_CREDENTIALS",
                "checklist_item_name": "Receive credentials",
            },
            {
                "task_type": "TYPE_A",
                "action": "COMPLETE",
                "checklist_item_name": "Complete",
            },
        ]
    }
    path = tmp_path / "checklist_mapping.yaml"
    path.write_text(yaml.dump(mapping), encoding="utf-8")
    return path


@pytest.fixture
def tmp_fields_yaml(tmp_path: Path) -> Path:
    fields = {
        "fields": [
            {"fieldName": "companyName", "fieldType": "TEXT"},
            {"fieldName": "workRequestNumber", "fieldType": "TEXT"},
            {"fieldName": "routingInternal", "fieldType": "TEXT"},
            {"fieldName": "routingExternal", "fieldType": "TEXT"},
            {"fieldName": "processThroughStateAgency", "fieldType": "TEXT"},
            {"fieldName": "receiveCredentials", "fieldType": "TEXT"},
            {"fieldName": "complete", "fieldType": "TEXT"},
            {"fieldName": "additionalNotes", "fieldType": "TEXT"},
            {"fieldName": "completedBy", "fieldType": "TEXT"},
            {"fieldName": "date", "fieldType": "DATE"},
        ]
    }
    path = tmp_path / "haul_ocr_fields.yaml"
    path.write_text(yaml.dump(fields), encoding="utf-8")
    return path


@pytest.fixture
def settings(tmp_path: Path, tmp_mapping_yaml: Path, tmp_fields_yaml: Path) -> Settings:
    return Settings(
        local_storage_dir=str(tmp_path / "storage"),
        # Give LocalStorage a fake base URL so generate_accessible_url works in tests
        local_storage_base_url="http://localhost:9999/files",
        review_queue_dir=str(tmp_path / "review"),
        haul_ocr_api_key="test-key",
        haul_ocr_poll_interval_seconds=0.0,
        haul_ocr_max_attempts=3,
        missing_checklist_item_policy="review",
        company_prefix_validation_required=False,
        checklist_mapping_path=str(tmp_mapping_yaml),
        haul_ocr_fields_path=str(tmp_fields_yaml),
        company_prefix_mapping_path=str(tmp_path / "company_prefix_mapping.yaml"),
    )


# ── Infrastructure mocks ──────────────────────────────────────────────────────

@pytest.fixture
def mock_cp(settings: Settings) -> MockCpSuiteClient:
    client = MockCpSuiteClient()
    wr = client.add_work_request("PFG-WR-351", client_name="Pacific First Group")
    task = client.add_task(wr.work_request_id, "TYPE_A", name="State Agency Task")
    client.add_checklist_item(task.task_id, "Process through state agency")
    client.add_checklist_item(task.task_id, "Receive credentials")
    client.add_checklist_item(task.task_id, "Complete")
    return client


@pytest.fixture
def idempotency_store(tmp_path: Path) -> LocalIdempotencyStore:
    return LocalIdempotencyStore(db_path=tmp_path / "idempotency.db")


@pytest.fixture
def review_queue(tmp_path: Path) -> LocalReviewQueue:
    return LocalReviewQueue(queue_dir=tmp_path / "review")


@pytest.fixture
def local_storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(
        base_dir=tmp_path / "storage",
        base_url="http://localhost:9999/files",
    )


# ── Use case builder ──────────────────────────────────────────────────────────

def build_use_case(
    settings: Settings,
    mock_ocr: MockOcrClient,
    mock_cp: MockCpSuiteClient,
    idempotency_store: LocalIdempotencyStore,
    review_queue: LocalReviewQueue,
    local_storage: LocalStorage,
) -> ProcessScanUseCase:
    return ProcessScanUseCase(
        storage=local_storage,
        pdf_processor=PypdfProcessor(),
        ocr_client=mock_ocr,
        cp_suite=mock_cp,
        idempotency_store=idempotency_store,
        review_queue=review_queue,
        checklist_mapper=ChecklistMapper(settings.checklist_mapping_path),
        audit_note_builder=AuditNoteBuilder(),
        settings=settings,
    )


@pytest.fixture
def valid_ocr_client() -> MockOcrClient:
    return MockOcrClient(
        extracted_info={
            "companyName": "Pacific First Group",
            "workRequestNumber": "PFG-WR-351",
            "routingInternal": "x",
            "routingExternal": "",
            "processThroughStateAgency": "x",
            "receiveCredentials": "",
            "complete": "",
            "additionalNotes": "",
            "completedBy": "Jane Smith",
            "date": "2024-01-15",
        },
        status="COMPLETED",
    )
