from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from .enums import CoverSheetAction, ProcessingState, ReviewReasonCode, RoutingType


class OcrField(BaseModel):
    """A single field definition sent to the OCR service."""

    field_name: str
    field_type: str  # e.g. "TEXT" | "DATE"


class OcrFieldConfidence(BaseModel):
    field_name: str
    confidence: float


class CoverSheet(BaseModel):
    """Typed domain model for the LCPT scan cover sheet after OCR extraction.

    The current LCPT cover sheet has NO Company Name field -- it was removed
    in the 2026 redesign. Do not re-introduce company_name without updating
    the cover sheet template first.
    """

    work_request_number: Optional[str] = None
    routing: Optional[RoutingType] = None
    checked_actions: list[CoverSheetAction] = Field(default_factory=list)
    additional_notes: Optional[str] = None
    completed_by: Optional[str] = None
    scan_date: Optional[date] = None  # renamed from 'date' to avoid shadowing datetime.date
    # Populated only if the OCR service returns per-field confidence scores
    field_confidences: list[OcrFieldConfidence] = Field(default_factory=list)
    raw_ocr: Optional[dict[str, Any]] = None


class OcrSubmissionResult(BaseModel):
    request_id: str
    status: str


class OcrResult(BaseModel):
    request_id: str
    status: str
    extracted_info: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    raw_response: Optional[dict[str, Any]] = None


class ScanRecord(BaseModel):
    """Persisted state for one scan -- drives idempotency and audit trail."""

    scan_id: str
    source_path: str
    source_etag: Optional[str] = None
    state: ProcessingState = ProcessingState.RECEIVED
    ocr_request_id: Optional[str] = None
    work_request_number: Optional[str] = None
    correlation_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkRequest(BaseModel):
    """Minimal CP Suite Work Request representation.

    Maps from CP Suite WorkRequestDto:
        workRequestId        -> work_request_id
        displayId            -> work_request_number (e.g. PFG-WR-351)
        title                -> title
        locationId           -> location_id
        clientRootLocationId -> client_root_location_id
    """

    work_request_id: str
    work_request_number: str
    client_name: Optional[str] = None
    title: Optional[str] = None
    location_id: Optional[str] = None
    client_root_location_id: Optional[str] = None
    raw: Optional[dict[str, Any]] = None


class ChecklistItem(BaseModel):
    """Maps from CP Suite CpTaskChecklistItemDto.

    cpTaskChecklistItemId -> item_id
    checklistItem         -> name
    isCompleted           -> is_complete
    cpTaskId              -> task_id
    """

    item_id: str
    name: str
    is_complete: bool = False
    task_id: str


class Task(BaseModel):
    """Maps from CP Suite WorkRequestTaskDto.

    cpTaskId    -> task_id
    serviceName -> task_type (human-readable; used for checklist mapping)
    taskTypeId  -> task_type_id
    title       -> name
    """

    task_id: str
    task_type: str
    name: str
    task_type_id: Optional[str] = None
    checklist_items: list[ChecklistItem] = Field(default_factory=list)


class ReviewItem(BaseModel):
    """Everything a human reviewer needs to assess a failed scan."""

    scan_id: str
    source_path: str
    reason_code: ReviewReasonCode
    message: str
    extracted_cover_sheet: Optional[CoverSheet] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    processing_state: ProcessingState
    correlation_id: str


class ObjectMetadata(BaseModel):
    """Metadata for an object in storage (S3 or local)."""

    bucket: Optional[str] = None
    key: str
    etag: Optional[str] = None
    size: Optional[int] = None
    last_modified: Optional[datetime] = None
    content_type: Optional[str] = None


class ScanEvent(BaseModel):
    """Trigger event -- either a local file path or an S3 object key."""

    source_path: str
    etag: Optional[str] = None
    scan_id: Optional[str] = None
    correlation_id: Optional[str] = None
