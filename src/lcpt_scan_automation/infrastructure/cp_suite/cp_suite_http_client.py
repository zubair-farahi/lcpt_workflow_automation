"""CP Suite TaskManager HTTP client.

Implemented against the staging OpenAPI spec (TaskManager.API 1.0) and the
auth details provided by the CP Suite team.

Base URL (staging): https://cp3-staging.itscomply.com/task-manager-api
Auth: Bearer (JWT) via CpSuiteTokenProvider (OAuth2 password grant).

Confirmed endpoints used (NOTE: the Swagger spec lists these under /api/ but
the live API serves them WITHOUT the /api/ segment — confirmed empirically
against staging on 2026-06-09. Base URL already includes the /task-manager-api
mount prefix, e.g. https://cp3-staging.itscomply.com/task-manager-api):
    GET   /work-requests/{displayId}                      -> WorkRequestDto
    GET   /work-requests/{workRequestId}/tasks            -> WorkRequestTasksDto
    GET   /tasks/{cpTaskId}/checklistItems                -> CpTaskChecklistItemDto[]
    PATCH /tasks/{cpTaskId}/checklistItems/{cpTaskChecklistItemId}   -> 200
          (Swagger says /tasks/checklistItems/{id} (flat) but the live
           API uses the nested /tasks/{taskId}/checklistItems/{itemId}
           form — confirmed by watching the UI network tab on 2026-06-09.)
    POST  /work-requests/{workRequestId}/system-notes     -> WorkRequestNoteDto
    POST  /work-request-files/{fileCategory}/{locationId}/{objectId}/{parentObjectId}
    POST  /work-request-internal-files/{fileCategory}/{locationId}/{objectId}/{parentObjectId}

TODO (attachment endpoints — confirm with CP Suite before production use):
    - exact value of {fileCategory}
    - whether {objectId} is the workRequestId and what {parentObjectId} should be
    - whether userId is required in the multipart body
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from ...domain.errors import CpSuiteAuthError, CpSuiteError, CpSuiteNotFoundError
from ...domain.models import ChecklistItem, Task, WorkRequest
from .auth import CpSuiteTokenProvider

log = structlog.get_logger()


class CpSuiteHttpClient:
    """Concrete CpSuiteClientPort implementation for the TaskManager API."""

    def __init__(
        self,
        base_url: str,
        token_provider: CpSuiteTokenProvider,
        file_category: str = "Document",
        user_id: str = "",
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._tokens = token_provider
        self._file_category = file_category
        self._user_id = user_id or None
        self._client = httpx.Client(timeout=timeout_seconds)

    # ── Work Request ───────────────────────────────────────────────────────────

    def get_work_request(self, wr_number: str) -> WorkRequest:
        resp = self._request("GET", f"/work-requests/{wr_number}")
        data = self._parse_json(resp)
        return WorkRequest(
            work_request_id=data["workRequestId"],
            work_request_number=data.get("displayId") or wr_number,
            title=data.get("title"),
            client_name=data.get("title"),
            location_id=data.get("locationId"),
            client_root_location_id=data.get("clientRootLocationId"),
            raw=data,
        )

    # ── Tasks ──────────────────────────────────────────────────────────────────

    def get_tasks(self, wr_id: str) -> list[Task]:
        resp = self._request("GET", f"/work-requests/{wr_id}/tasks")
        data = self._parse_json(resp)
        tasks_raw = data.get("workRequestTasks") or []
        return [
            Task(
                task_id=t["cpTaskId"],
                # serviceName is the human-readable type used for checklist mapping;
                # fall back to taskTypeId if serviceName is absent.
                task_type=t.get("serviceName") or t.get("taskTypeId") or "",
                name=t.get("title") or "",
                task_type_id=t.get("taskTypeId"),
            )
            for t in tasks_raw
        ]

    # ── Checklist items ────────────────────────────────────────────────────────

    def get_checklist_items(self, task_id: str) -> list[ChecklistItem]:
        resp = self._request("GET", f"/tasks/{task_id}/checklistItems")
        items = self._parse_json(resp)
        if not isinstance(items, list):
            raise CpSuiteError(f"Expected a list of checklist items, got: {type(items)}")
        return [
            ChecklistItem(
                item_id=item["cpTaskChecklistItemId"],
                name=item.get("checklistItem") or "",
                is_complete=bool(item.get("isCompleted", False)),
                task_id=item.get("cpTaskId") or task_id,
            )
            for item in items
        ]

    def mark_checklist_item_complete(self, task_id: str, item_id: str) -> None:
        """Mark a single checklist item as complete.

        The live API expects RFC 6902 JSON Patch (an array of ops), NOT the
        flat `{ "updatedFields": [...] }` shape that the Swagger spec
        suggested. Confirmed empirically against staging on 2026-06-09 —
        the flat shape produced HTTP 500 "JSON patch document was malformed".
        """
        now = datetime.now(tz=timezone.utc).isoformat()
        patch_doc = [
            {"op": "replace", "path": "/isCompleted", "value": True},
            {"op": "replace", "path": "/completedDate", "value": now},
        ]
        self._request(
            "PATCH",
            f"/tasks/{task_id}/checklistItems/{item_id}",
            json=patch_doc,
            extra_headers={"Content-Type": "application/json-patch+json"},
        )

    # ── System note ────────────────────────────────────────────────────────────

    def add_system_note(self, wr_id: str, note: str) -> None:
        body: dict[str, Any] = {"workRequestId": wr_id, "note": note}
        self._request("POST", f"/work-requests/{wr_id}/system-notes", json=body)

    # ── Attachments ────────────────────────────────────────────────────────────

    def attach_pdf_internal(self, work_request: WorkRequest, pdf_bytes: bytes, filename: str) -> None:
        self._attach(work_request, pdf_bytes, filename, internal=True)

    def attach_pdf_external(self, work_request: WorkRequest, pdf_bytes: bytes, filename: str) -> None:
        self._attach(work_request, pdf_bytes, filename, internal=False)

    def _attach(
        self,
        work_request: WorkRequest,
        pdf_bytes: bytes,
        filename: str,
        *,
        internal: bool,
    ) -> None:
        # TODO: confirm these path params with CP Suite.
        #   fileCategory  - using configured value (default "Document")
        #   locationId    - using the WR's locationId
        #   objectId      - using the workRequestId
        #   parentObjectId- using the workRequestId (best guess)
        location_id = work_request.location_id
        if not location_id:
            raise CpSuiteError(
                "Cannot attach PDF: WorkRequest is missing locationId "
                "(required by the attachment endpoint)."
            )
        object_id = work_request.work_request_id
        parent_object_id = work_request.work_request_id

        prefix = "work-request-internal-files" if internal else "work-request-files"
        path = f"/{prefix}/{self._file_category}/{location_id}/{object_id}/{parent_object_id}"

        files = {"file": (filename, pdf_bytes, "application/pdf")}
        form: dict[str, str] = {"objectDisplayName": filename, "setAsPrimary": "false"}
        if self._user_id:
            form["userId"] = self._user_id

        self._request("POST", path, files=files, data=form)

    # ── Low-level request helper ───────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        files: Any = None,
        data: Any = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        url = f"{self._base}{path}"
        headers = {"Authorization": f"Bearer {self._tokens.get_token()}"}
        if json is not None:
            headers["Content-Type"] = "application/json"
        # Callers can override or add headers (e.g. application/json-patch+json
        # for RFC 6902 PATCH bodies). Override last so it wins over defaults.
        if extra_headers:
            headers.update(extra_headers)
        try:
            resp = self._client.request(
                method, url, headers=headers, json=json, files=files, data=data
            )
        except httpx.TimeoutException as exc:
            raise CpSuiteError(f"CP Suite {method} {path} timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise CpSuiteError(f"CP Suite {method} {path} network error: {exc}") from exc

        self._raise_for_status(resp, method, path)
        return resp

    def _raise_for_status(self, resp: httpx.Response, method: str, path: str) -> None:
        if resp.status_code in (401, 403):
            raise CpSuiteAuthError(
                f"CP Suite auth failed ({resp.status_code}) on {method} {path}. "
                "Check token / user permissions."
            )
        if resp.status_code == 404:
            raise CpSuiteNotFoundError(f"CP Suite resource not found: {method} {path}")
        if resp.status_code >= 400:
            raise CpSuiteError(
                f"CP Suite {method} {path} returned {resp.status_code}: {resp.text[:500]}"
            )

    @staticmethod
    def _parse_json(resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except Exception as exc:
            raise CpSuiteError(
                f"CP Suite returned non-JSON ({resp.status_code}): {resp.text[:200]}"
            ) from exc
