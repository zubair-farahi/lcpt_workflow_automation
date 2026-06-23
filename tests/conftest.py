"""Shared fixtures for all tests."""

from __future__ import annotations

import io
import json
import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml
from pypdf import PdfWriter

from lcpt_scan_automation.application.audit_note_builder import AuditNoteBuilder
from lcpt_scan_automation.application.checklist_mapper import ChecklistMapper
from lcpt_scan_automation.application.process_scan import ProcessScanUseCase
from lcpt_scan_automation.config.settings import Settings
from lcpt_scan_automation.domain.errors import CpSuiteNotFoundError, StorageError
from lcpt_scan_automation.domain.models import ChecklistItem, OcrResult, OcrSubmissionResult, ReviewItem, Task, WorkRequest
from lcpt_scan_automation.infrastructure.idempotency.memory_idempotency_store import MemoryIdempotencyStore
from lcpt_scan_automation.infrastructure.pdf.pypdf_processor import PypdfProcessor


def compute_file_hash(path: str | Path) -> str:
    file_path = Path(path)
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LocalStorage:
    def __init__(self, base_dir: str | Path, base_url: str = "http://localhost:9999/files") -> None:
        self._base = Path(base_dir)
        self._base_url = base_url.rstrip("/")

    def read_bytes(self, path: str) -> bytes:
        full = self._resolve(path)
        if not full.exists():
            raise StorageError(f"File not found: {full}")
        return full.read_bytes()

    def write_bytes(self, path: str, data: bytes) -> None:
        full = self._resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def generate_accessible_url(self, path: str, expires_in_seconds: int = 3600) -> str:
        return f"{self._base_url}/{Path(path).name}"

    def delete(self, path: str) -> None:
        full = self._resolve(path)
        if full.exists():
            full.unlink()

    def _resolve(self, path: str) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self._base / path


class MockOcrClient:
    def __init__(self, extracted_info: dict[str, Any] | None = None, status: str = "COMPLETED") -> None:
        self.extracted_info = extracted_info or {}
        self.status = status
        self.submitted_urls: list[str] = []

    def submit_document(self, document_url, fields):
        self.submitted_urls.append(document_url)
        return OcrSubmissionResult(request_id="mock-ocr-request", status="QUEUED")

    def get_result(self, request_id: str) -> OcrResult:
        return OcrResult(
            request_id=request_id,
            status=self.status,
            extracted_info=self.extracted_info,
        )


class MockCpSuiteClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._work_requests: dict[str, WorkRequest] = {}
        self._tasks_by_wr: dict[str, list[Task]] = {}
        self._items_by_task: dict[str, list[ChecklistItem]] = {}

    def add_work_request(self, wr_number: str, client_name: str | None = None) -> WorkRequest:
        wr = WorkRequest(
            work_request_id=f"wr-{len(self._work_requests) + 1}",
            work_request_number=wr_number,
            client_name=client_name,
            title=client_name,
            location_id="loc-1",
            client_root_location_id="root-loc-1",
        )
        self._work_requests[wr_number] = wr
        self._tasks_by_wr[wr.work_request_id] = []
        return wr

    def add_task(self, wr_id: str, task_type: str, name: str = "Task") -> Task:
        task = Task(task_id=f"task-{len(self._items_by_task) + 1}", task_type=task_type, name=name)
        self._tasks_by_wr.setdefault(wr_id, []).append(task)
        self._items_by_task[task.task_id] = []
        return task

    def add_checklist_item(self, task_id: str, name: str) -> ChecklistItem:
        item = ChecklistItem(
            item_id=f"item-{len(self._items_by_task.get(task_id, [])) + 1}",
            name=name,
            is_complete=False,
            task_id=task_id,
        )
        self._items_by_task.setdefault(task_id, []).append(item)
        return item

    def get_work_request(self, wr_number: str) -> WorkRequest:
        self.calls.append({"method": "get_work_request", "wr_number": wr_number})
        try:
            return self._work_requests[wr_number]
        except KeyError as exc:
            raise CpSuiteNotFoundError(wr_number) from exc

    def attach_pdf_internal(self, work_request: WorkRequest, pdf_bytes: bytes, filename: str) -> None:
        self.calls.append({"method": "attach_pdf_internal", "filename": filename})

    def attach_pdf_external(self, work_request: WorkRequest, pdf_bytes: bytes, filename: str) -> None:
        self.calls.append({"method": "attach_pdf_external", "filename": filename})

    def get_tasks(self, wr_id: str) -> list[Task]:
        self.calls.append({"method": "get_tasks", "wr_id": wr_id})
        return list(self._tasks_by_wr.get(wr_id, []))

    def get_checklist_items(self, task_id: str) -> list[ChecklistItem]:
        self.calls.append({"method": "get_checklist_items", "task_id": task_id})
        return list(self._items_by_task.get(task_id, []))

    def mark_checklist_item_complete(self, task_id: str, item_id: str) -> None:
        self.calls.append({"method": "mark_checklist_item_complete", "task_id": task_id, "item_id": item_id})
        for item in self._items_by_task.get(task_id, []):
            if item.item_id == item_id:
                item.is_complete = True

    def add_system_note(self, wr_id: str, note: str) -> None:
        self.calls.append({"method": "add_system_note", "wr_id": wr_id, "note": note})


class LocalReviewQueue:
    def __init__(self, queue_dir: str | Path) -> None:
        self._queue_dir = Path(queue_dir)
        self._queue_dir.mkdir(parents=True, exist_ok=True)
        self.items: list[ReviewItem] = []

    def enqueue(self, item: ReviewItem) -> None:
        self.items.append(item)
        filename = f"{item.scan_id}_{item.reason_code}.json"
        (self._queue_dir / filename).write_text(
            json.dumps(item.model_dump(mode="json"), indent=2, default=str),
            encoding="utf-8",
        )


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
                "action": "SEND_CREDENTIALS",
                "checklist_item_name": "Send credentials",
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
            {"fieldName": "workRequestNumber", "fieldType": "TEXT"},
            {"fieldName": "attachDocumentsToInternalAttachments", "fieldType": "TEXT"},
            {"fieldName": "attachDocumentsToAttachments", "fieldType": "TEXT"},
            {"fieldName": "processThroughStateAgency", "fieldType": "TEXT"},
            {"fieldName": "receiveCredentials", "fieldType": "TEXT"},
            {"fieldName": "sendCredentials", "fieldType": "TEXT"},
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
        haul_ocr_api_key="test-key",
        haul_ocr_poll_interval_seconds=0.0,
        haul_ocr_max_attempts=3,
        missing_checklist_item_policy="review",
        checklist_mapping_path=str(tmp_mapping_yaml),
        haul_ocr_fields_path=str(tmp_fields_yaml),
    )


# ── Infrastructure mocks ──────────────────────────────────────────────────────

@pytest.fixture
def mock_cp(settings: Settings) -> MockCpSuiteClient:
    client = MockCpSuiteClient()
    wr = client.add_work_request("PFG-WR-351", client_name="Pacific First Group")
    task = client.add_task(wr.work_request_id, "TYPE_A", name="State Agency Task")
    client.add_checklist_item(task.task_id, "Process through state agency")
    client.add_checklist_item(task.task_id, "Receive credentials")
    client.add_checklist_item(task.task_id, "Send credentials")
    return client


@pytest.fixture
def idempotency_store() -> MemoryIdempotencyStore:
    return MemoryIdempotencyStore()


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
    idempotency_store: MemoryIdempotencyStore,
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
            "workRequestNumber": "PFG-WR-351",
            "attachDocumentsToInternalAttachments": "x",
            "attachDocumentsToAttachments": "",
            "processThroughStateAgency": "x",
            "receiveCredentials": "",
            "sendCredentials": "",
            "additionalNotes": "",
            "completedBy": "Jane Smith",
            "date": "2024-01-15",
        },
        status="COMPLETED",
    )
