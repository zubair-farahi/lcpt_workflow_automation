"""Mock CP Suite client for local development and tests."""

from __future__ import annotations

from typing import Optional
from uuid import uuid4

from ...domain.errors import CpSuiteNotFoundError
from ...domain.models import ChecklistItem, Task, WorkRequest


class MockCpSuiteClient:
    """In-memory CP Suite client.

    Pre-load work requests and tasks via the builder methods or the constructor.
    Recorded calls are stored on self.calls for assertion in tests.
    """

    def __init__(self) -> None:
        self._work_requests: dict[str, WorkRequest] = {}  # keyed by wr_number
        self._tasks: dict[str, list[Task]] = {}           # keyed by wr_id
        self._checklist_items: dict[str, list[ChecklistItem]] = {}  # keyed by task_id
        self.calls: list[dict] = []

    # ── Builder helpers ────────────────────────────────────────────────────────

    def add_work_request(
        self,
        wr_number: str,
        wr_id: Optional[str] = None,
        client_name: Optional[str] = None,
    ) -> WorkRequest:
        wr = WorkRequest(
            work_request_id=wr_id or str(uuid4()),
            work_request_number=wr_number,
            client_name=client_name,
        )
        self._work_requests[wr_number] = wr
        return wr

    def add_task(
        self,
        wr_id: str,
        task_type: str,
        task_id: Optional[str] = None,
        name: str = "",
    ) -> Task:
        task = Task(
            task_id=task_id or str(uuid4()),
            task_type=task_type,
            name=name or task_type,
        )
        self._tasks.setdefault(wr_id, []).append(task)
        return task

    def add_checklist_item(
        self,
        task_id: str,
        name: str,
        item_id: Optional[str] = None,
        is_complete: bool = False,
    ) -> ChecklistItem:
        item = ChecklistItem(
            item_id=item_id or str(uuid4()),
            name=name,
            is_complete=is_complete,
            task_id=task_id,
        )
        self._checklist_items.setdefault(task_id, []).append(item)
        return item

    # ── CpSuiteClientPort implementation ──────────────────────────────────────

    def get_work_request(self, wr_number: str) -> WorkRequest:
        self.calls.append({"method": "get_work_request", "wr_number": wr_number})
        if wr_number not in self._work_requests:
            raise CpSuiteNotFoundError(f"Work Request '{wr_number}' not found (mock)")
        return self._work_requests[wr_number]

    def attach_pdf_internal(self, work_request: WorkRequest, pdf_bytes: bytes, filename: str) -> None:
        self.calls.append(
            {"method": "attach_pdf_internal", "wr_id": work_request.work_request_id, "filename": filename}
        )

    def attach_pdf_external(self, work_request: WorkRequest, pdf_bytes: bytes, filename: str) -> None:
        self.calls.append(
            {"method": "attach_pdf_external", "wr_id": work_request.work_request_id, "filename": filename}
        )

    def get_tasks(self, wr_id: str) -> list[Task]:
        self.calls.append({"method": "get_tasks", "wr_id": wr_id})
        return list(self._tasks.get(wr_id, []))

    def get_checklist_items(self, task_id: str) -> list[ChecklistItem]:
        self.calls.append({"method": "get_checklist_items", "task_id": task_id})
        return list(self._checklist_items.get(task_id, []))

    def mark_checklist_item_complete(self, task_id: str, item_id: str) -> None:
        self.calls.append({"method": "mark_checklist_item_complete", "task_id": task_id, "item_id": item_id})
        for item in self._checklist_items.get(task_id, []):
            if item.item_id == item_id:
                # ChecklistItem is a Pydantic model; replace with updated copy
                idx = self._checklist_items[task_id].index(item)
                self._checklist_items[task_id][idx] = item.model_copy(update={"is_complete": True})
                return

    def add_system_note(self, wr_id: str, note: str) -> None:
        self.calls.append({"method": "add_system_note", "wr_id": wr_id, "note": note})
