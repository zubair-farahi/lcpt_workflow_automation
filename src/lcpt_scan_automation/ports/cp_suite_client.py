from typing import Protocol, runtime_checkable

from ..domain.models import ChecklistItem, Task, WorkRequest


@runtime_checkable
class CpSuiteClientPort(Protocol):
    def get_work_request(self, wr_number: str) -> WorkRequest:
        """Fetch a Work Request by its human-readable number / displayId (e.g. PFG-WR-351).

        Raises CpSuiteNotFoundError if the WR does not exist.
        Raises CpSuiteError for other API failures.
        """
        ...

    def attach_pdf_internal(
        self,
        work_request: WorkRequest,
        pdf_bytes: bytes,
        filename: str,
    ) -> None:
        """Attach a PDF to the Work Request as an Internal attachment.

        Takes the full WorkRequest because the CP Suite attachment endpoint
        needs locationId / objectId derived from it.
        """
        ...

    def attach_pdf_external(
        self,
        work_request: WorkRequest,
        pdf_bytes: bytes,
        filename: str,
    ) -> None:
        """Attach a PDF to the Work Request as an External attachment."""
        ...

    def get_tasks(self, wr_id: str) -> list[Task]:
        """Return all tasks for a Work Request."""
        ...

    def get_checklist_items(self, task_id: str) -> list[ChecklistItem]:
        """Return all checklist items for a single task."""
        ...

    def mark_checklist_item_complete(self, task_id: str, item_id: str) -> None:
        """Mark a checklist item as complete."""
        ...

    def add_system_note(self, wr_id: str, note: str) -> None:
        """Write an audit/system note to the Work Request."""
        ...
