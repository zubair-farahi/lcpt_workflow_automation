"""Unit tests for SharePointReviewQueue.

Uses respx to mock the Microsoft Graph + login endpoints -- no real
network calls.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

import httpx
import pytest
import respx

from lcpt_scan_automation.domain.enums import ProcessingState, ReviewReasonCode
from lcpt_scan_automation.domain.errors import ReviewQueueError, StorageError
from lcpt_scan_automation.domain.models import ReviewItem
from lcpt_scan_automation.infrastructure.review_queue.sharepoint_review_queue import (
    GRAPH_BASE,
    LOGIN_BASE,
    SharePointReviewQueue,
    _GraphTokenProvider,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

TENANT = "fake-tenant"
CLIENT_ID = "fake-client-id"
CLIENT_SECRET = "fake-secret"
SITE_ID = "fake.sharepoint.com,site1,web1"
DRIVE_ID = "b!fake-drive-id"


def _make_item(
    *,
    source_path: str = "UploadedFromSharedcifs/cpegg/scan.pdf",
    reason_code: ReviewReasonCode = ReviewReasonCode.UNEXPECTED_ERROR,
) -> ReviewItem:
    return ReviewItem(
        scan_id="scan-abc-123",
        source_path=source_path,
        reason_code=reason_code,
        message="OCR returned no work request number",
        extracted_cover_sheet=None,
        timestamp=datetime(2026, 6, 15, 12, 0, 0),
        processing_state=ProcessingState.REVIEW_REQUIRED,
        correlation_id="corr-abc-123",
    )


class _FakeStorage:
    """Minimal StoragePort stand-in for tests."""

    def __init__(
        self,
        *,
        contents: Optional[dict[str, bytes]] = None,
        read_raises: Optional[Exception] = None,
    ) -> None:
        self._contents = contents or {}
        self._read_raises = read_raises

    def read_bytes(self, path: str) -> bytes:
        if self._read_raises is not None:
            raise self._read_raises
        return self._contents.get(path, b"%PDF-1.4 fake")

    def write_bytes(self, path: str, data: bytes) -> None:  # pragma: no cover
        pass

    def exists(self, path: str) -> bool:  # pragma: no cover
        return True

    def generate_accessible_url(  # pragma: no cover
        self, path: str, expires_in_seconds: int = 3600
    ) -> str:
        return f"https://fake/{path}"


def _build_queue(
    *,
    storage: Optional[_FakeStorage] = None,
    column_names: Optional[dict[str, str]] = None,
) -> SharePointReviewQueue:
    return SharePointReviewQueue(
        tenant_id=TENANT,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        site_id=SITE_ID,
        drive_id=DRIVE_ID,
        storage=storage or _FakeStorage(),
        column_names=column_names,
    )


def _mock_token(respx_mock: respx.MockRouter) -> respx.Route:
    return respx_mock.post(
        f"{LOGIN_BASE}/{TENANT}/oauth2/v2.0/token"
    ).mock(
        return_value=httpx.Response(
            200, json={"access_token": "fake-bearer", "expires_in": 3600}
        )
    )


def _upload_url() -> str:
    return (
        f"{GRAPH_BASE}/sites/{SITE_ID}/drives/{DRIVE_ID}"
        f"/root:/scan-abc-123_UNEXPECTED_ERROR.pdf:/content"
    )


def _metadata_url(drive_item_id: str = "drive-item-1") -> str:
    return (
        f"{GRAPH_BASE}/sites/{SITE_ID}/drives/{DRIVE_ID}"
        f"/items/{drive_item_id}/listItem/fields"
    )


# ── Token provider ────────────────────────────────────────────────────────────


class TestGraphTokenProvider:
    def test_raises_if_creds_missing(self) -> None:
        with pytest.raises(ReviewQueueError, match="Graph credentials missing"):
            _GraphTokenProvider(
                tenant_id="", client_id="x", client_secret="y"
            )

    @respx.mock
    def test_acquires_and_caches_token(self) -> None:
        route = _mock_token(respx.mock)
        p = _GraphTokenProvider(
            tenant_id=TENANT, client_id=CLIENT_ID, client_secret=CLIENT_SECRET
        )
        assert p.get_token() == "fake-bearer"
        assert p.get_token() == "fake-bearer"  # cached
        assert route.call_count == 1  # only one HTTP call despite two get_token()s

    @respx.mock
    def test_raises_on_token_failure(self) -> None:
        respx.mock.post(f"{LOGIN_BASE}/{TENANT}/oauth2/v2.0/token").mock(
            return_value=httpx.Response(401, text="invalid_client")
        )
        p = _GraphTokenProvider(
            tenant_id=TENANT, client_id=CLIENT_ID, client_secret=CLIENT_SECRET
        )
        with pytest.raises(ReviewQueueError, match="Graph token acquisition failed"):
            p.get_token()


# ── Happy path: enqueue uploads PDF + sets metadata ───────────────────────────


class TestEnqueueHappyPath:
    @respx.mock
    def test_uploads_pdf_and_patches_metadata(self) -> None:
        _mock_token(respx.mock)
        upload = respx.mock.put(_upload_url()).mock(
            return_value=httpx.Response(201, json={"id": "drive-item-1"})
        )
        patch = respx.mock.patch(_metadata_url()).mock(
            return_value=httpx.Response(200, json={})
        )

        storage = _FakeStorage(
            contents={"UploadedFromSharedcifs/cpegg/scan.pdf": b"%PDF-1.4 real"}
        )
        q = _build_queue(storage=storage)

        # Should not raise
        q.enqueue(_make_item())

        assert upload.called
        assert patch.called
        # The PDF bytes from storage made it to SharePoint
        assert upload.calls.last.request.content == b"%PDF-1.4 real"

    @respx.mock
    def test_metadata_payload_has_all_columns(self) -> None:
        _mock_token(respx.mock)
        respx.mock.put(_upload_url()).mock(
            return_value=httpx.Response(201, json={"id": "drive-item-1"})
        )
        patch = respx.mock.patch(_metadata_url()).mock(
            return_value=httpx.Response(200, json={})
        )

        _build_queue().enqueue(_make_item())

        body = json.loads(patch.calls.last.request.content)
        assert body["Status"] == "New"
        assert body["ReasonCode"] == "UNEXPECTED_ERROR"
        assert body["ReasonDetails"] == "OCR returned no work request number"
        assert body["Rep"] == "cpegg"  # second path segment
        assert body["ScanID"] == "scan-abc-123"
        assert body["SourceS3Key"] == "UploadedFromSharedcifs/cpegg/scan.pdf"
        assert body["WRNumber"] == ""  # no cover sheet -> empty
        assert body["ExtractedFields"] == ""

    @respx.mock
    def test_uses_pdf_content_type_header(self) -> None:
        _mock_token(respx.mock)
        upload = respx.mock.put(_upload_url()).mock(
            return_value=httpx.Response(201, json={"id": "drive-item-1"})
        )
        respx.mock.patch(_metadata_url()).mock(return_value=httpx.Response(200))

        _build_queue().enqueue(_make_item())

        assert upload.calls.last.request.headers["content-type"] == "application/pdf"
        assert upload.calls.last.request.headers["authorization"] == "Bearer fake-bearer"

    @respx.mock
    def test_custom_column_names_override(self) -> None:
        """If the tenant uses encoded names (Reason_x0020_Code), allow override."""
        _mock_token(respx.mock)
        respx.mock.put(_upload_url()).mock(
            return_value=httpx.Response(201, json={"id": "drive-item-1"})
        )
        patch = respx.mock.patch(_metadata_url()).mock(
            return_value=httpx.Response(200)
        )

        q = _build_queue(
            column_names={
                "reason_code": "Reason_x0020_Code",
                "wr_number": "WR_x0020_Number",
            }
        )
        q.enqueue(_make_item())

        body = json.loads(patch.calls.last.request.content)
        assert "Reason_x0020_Code" in body
        assert "ReasonCode" not in body  # default was overridden
        assert "WR_x0020_Number" in body


# ── Error paths ───────────────────────────────────────────────────────────────


class TestEnqueueErrorPaths:
    @respx.mock
    def test_pdf_read_failure_falls_back_to_json(self) -> None:
        """If storage can't return the PDF, upload a JSON placeholder rather
        than losing the review item entirely.
        """
        _mock_token(respx.mock)
        upload = respx.mock.put(_upload_url()).mock(
            return_value=httpx.Response(201, json={"id": "drive-item-1"})
        )
        respx.mock.patch(_metadata_url()).mock(return_value=httpx.Response(200))

        storage = _FakeStorage(read_raises=StorageError("S3 NoSuchKey"))
        _build_queue(storage=storage).enqueue(_make_item())

        # File was still uploaded -- just with JSON content instead of PDF
        assert upload.called
        body = upload.calls.last.request.content
        parsed = json.loads(body)
        assert parsed["scan_id"] == "scan-abc-123"
        assert "Original PDF could not be read" in parsed["note"]

    @respx.mock
    def test_upload_failure_raises_review_queue_error(self) -> None:
        _mock_token(respx.mock)
        respx.mock.put(_upload_url()).mock(
            return_value=httpx.Response(403, text="Forbidden")
        )

        with pytest.raises(ReviewQueueError, match="SharePoint upload failed"):
            _build_queue().enqueue(_make_item())

    @respx.mock
    def test_metadata_failure_does_NOT_raise(self) -> None:
        """File was uploaded -- losing metadata isn't worth losing the
        whole review item over. Log loudly and move on.
        """
        _mock_token(respx.mock)
        respx.mock.put(_upload_url()).mock(
            return_value=httpx.Response(201, json={"id": "drive-item-1"})
        )
        respx.mock.patch(_metadata_url()).mock(
            return_value=httpx.Response(400, text="Invalid column name")
        )

        # Should NOT raise -- file is saved, just un-categorized
        _build_queue().enqueue(_make_item())

    def test_raises_if_site_or_drive_id_missing(self) -> None:
        with pytest.raises(ReviewQueueError, match="site_id/drive_id missing"):
            SharePointReviewQueue(
                tenant_id=TENANT,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                site_id="",
                drive_id=DRIVE_ID,
                storage=_FakeStorage(),
            )
