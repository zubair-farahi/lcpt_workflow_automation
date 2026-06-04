"""S3 storage adapter for LCPT Scan Automation.

Requires boto3 (install with: pip install 'lcpt-scan-automation[aws]').

Implements StoragePort for the use case (path-based, bytes in memory) plus
additional S3-specific methods for metadata, listing, and diagnostics.

TODO (open questions):
  1. Confirm whether HaulSafe OCR can access presigned S3 URLs.
  2. Confirm S3 bucket SSE encryption (SSE-S3 vs SSE-KMS) and KMS key access.
  3. Confirm whether the scoped IAM key has s3:PutObject for processing/ prefix.
  4. Confirm what prefix office scanners upload to (likely incoming/).
  5. Confirm lifecycle policy for processing/ temp files.
  6. Confirm if successfully processed scans should be moved/tagged or left in place.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

import structlog

from ...domain.errors import StorageError
from ...domain.models import ObjectMetadata

log = structlog.get_logger()


class S3Storage:
    """Full S3 storage adapter implementing StoragePort.

    The bucket is set at construction time; all path arguments are S3 keys
    (relative to the bucket root, no leading slash).
    """

    def __init__(
        self,
        bucket: str,
        region: str = "us-east-1",
        presigned_url_expiry_seconds: int = 900,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        client: Any = None,
    ) -> None:
        try:
            import boto3
        except ImportError as exc:
            raise ImportError(
                "boto3 is required for S3Storage. "
                "Install it with: pip install 'lcpt-scan-automation[aws]'"
            ) from exc

        self._bucket = bucket
        self._expiry = presigned_url_expiry_seconds

        if client is not None:
            self._client = client
        else:
            kwargs: dict[str, Any] = {"region_name": region}
            if aws_access_key_id and aws_secret_access_key:
                kwargs["aws_access_key_id"] = aws_access_key_id
                kwargs["aws_secret_access_key"] = aws_secret_access_key
            import boto3
            self._client = boto3.client("s3", **kwargs)

    # ── StoragePort interface ─────────────────────────────────────────────────

    def read_bytes(self, path: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=path)
            data = response["Body"].read()
            log.debug("s3_read", bucket=self._bucket, key=path, size=len(data))
            return data
        except Exception as exc:
            self._raise_storage_error(exc, "read", path)

    def write_bytes(self, path: str, data: bytes) -> None:
        try:
            self._client.put_object(Bucket=self._bucket, Key=path, Body=data)
            log.debug("s3_write", bucket=self._bucket, key=path, size=len(data))
        except Exception as exc:
            self._raise_storage_error(exc, "write", path)

    def exists(self, path: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=path)
            return True
        except Exception as exc:
            if self._is_not_found(exc):
                return False
            self._raise_storage_error(exc, "exists", path)

    def generate_accessible_url(self, path: str, expires_in_seconds: int = 3600) -> str:
        expiry = expires_in_seconds or self._expiry
        try:
            url = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": path},
                ExpiresIn=expiry,
            )
            log.debug("s3_presigned_url_generated", key=path, expiry_seconds=expiry)
            return url
        except Exception as exc:
            self._raise_storage_error(exc, "generate_presigned_url", path)

    # ── Richer S3-specific methods ────────────────────────────────────────────

    def get_object_metadata(self, key: str) -> ObjectMetadata:
        """Fetch S3 object metadata without downloading the body."""
        try:
            resp = self._client.head_object(Bucket=self._bucket, Key=key)
            return ObjectMetadata(
                bucket=self._bucket,
                key=key,
                etag=(resp.get("ETag") or "").strip('"'),
                size=resp.get("ContentLength"),
                last_modified=resp.get("LastModified"),
                content_type=resp.get("ContentType"),
            )
        except Exception as exc:
            self._raise_storage_error(exc, "head_object", key)

    def list_objects(
        self,
        prefix: str = "",
        max_keys: int = 100,
    ) -> list[ObjectMetadata]:
        """List objects under a prefix. Returns up to max_keys results."""
        try:
            resp = self._client.list_objects_v2(
                Bucket=self._bucket,
                Prefix=prefix,
                MaxKeys=max_keys,
            )
            return [
                ObjectMetadata(
                    bucket=self._bucket,
                    key=obj["Key"],
                    etag=(obj.get("ETag") or "").strip('"'),
                    size=obj.get("Size"),
                    last_modified=obj.get("LastModified"),
                )
                for obj in resp.get("Contents", [])
            ]
        except Exception as exc:
            self._raise_storage_error(exc, "list_objects", prefix)

    def delete_object(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
            log.debug("s3_deleted", bucket=self._bucket, key=key)
        except Exception as exc:
            self._raise_storage_error(exc, "delete_object", key)

    def build_idempotency_key(self, key: str, etag: Optional[str] = None) -> str:
        """Return bucket/key:etag — the canonical idempotency key for S3 objects."""
        return f"{self._bucket}/{key}:{etag or ''}"

    # ── Diagnostic methods (used by check-s3-access CLI command) ─────────────

    def diag_head_bucket(self) -> tuple[bool, str]:
        """Check s3:ListBucket permission via HeadBucket."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return True, f"s3:HeadBucket  ✓  s3://{self._bucket}"
        except Exception as exc:
            return False, f"s3:HeadBucket  ✗  {self._format_error(exc)}"

    def diag_list_objects(self, prefix: str = "") -> tuple[bool, str]:
        """Check s3:ListBucket permission via ListObjectsV2."""
        try:
            resp = self._client.list_objects_v2(
                Bucket=self._bucket, Prefix=prefix, MaxKeys=5
            )
            count = len(resp.get("Contents", []))
            return True, f"s3:ListBucket  ✓  found {count} object(s) under '{prefix}'"
        except Exception as exc:
            return False, f"s3:ListBucket  ✗  {self._format_error(exc)}"

    def diag_put_object(self, prefix: str = "diagnostics/") -> tuple[bool, str, Optional[str]]:
        """Check s3:PutObject by uploading a small test object.

        Returns (success, message, key) — key can be used to clean up.
        """
        key = f"{prefix}lcpt-access-check-{uuid.uuid4().hex[:8]}.txt"
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=b"lcpt-scan-automation access check",
                ContentType="text/plain",
            )
            return True, f"s3:PutObject   ✓  wrote s3://{self._bucket}/{key}", key
        except Exception as exc:
            return False, f"s3:PutObject   ✗  {self._format_error(exc)}", None

    def diag_get_object(self, key: str) -> tuple[bool, str]:
        """Check s3:GetObject by reading a specific key."""
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
            size = len(resp["Body"].read())
            return True, f"s3:GetObject   ✓  read {size} bytes from s3://{self._bucket}/{key}"
        except Exception as exc:
            return False, f"s3:GetObject   ✗  {self._format_error(exc)}"

    def diag_delete_object(self, key: str) -> tuple[bool, str]:
        """Check s3:DeleteObject by deleting a specific key."""
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
            return True, f"s3:DeleteObject✓  deleted s3://{self._bucket}/{key}"
        except Exception as exc:
            return False, f"s3:DeleteObject✗  {self._format_error(exc)}"

    # ── Error helpers ─────────────────────────────────────────────────────────

    def _raise_storage_error(self, exc: Exception, operation: str, key: str) -> None:
        msg = self._format_error(exc)
        raise StorageError(
            f"S3 {operation} failed for s3://{self._bucket}/{key}: {msg}"
        ) from exc

    @staticmethod
    def _format_error(exc: Exception) -> str:
        try:
            code = exc.response["Error"]["Code"]  # type: ignore[attr-defined]
            msg = exc.response["Error"]["Message"]  # type: ignore[attr-defined]
            if code in ("403", "AccessDenied"):
                return (
                    f"AccessDenied ({code}) — check IAM permissions "
                    f"(s3:GetObject / s3:PutObject / s3:ListBucket). Detail: {msg}"
                )
            return f"{code}: {msg}"
        except (AttributeError, KeyError, TypeError):
            return str(exc)

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        try:
            code = exc.response["Error"]["Code"]  # type: ignore[attr-defined]
            return code in ("404", "NoSuchKey")
        except (AttributeError, KeyError, TypeError):
            return False
