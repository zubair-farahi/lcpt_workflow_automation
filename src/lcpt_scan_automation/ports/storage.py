from typing import Protocol, runtime_checkable


@runtime_checkable
class StoragePort(Protocol):
    def read_bytes(self, path: str) -> bytes:
        """Read raw bytes from the given path (local or S3 key)."""
        ...

    def write_bytes(self, path: str, data: bytes) -> None:
        """Write raw bytes to the given path."""
        ...

    def exists(self, path: str) -> bool:
        """Return True if the path exists."""
        ...

    def generate_accessible_url(self, path: str, expires_in_seconds: int = 3600) -> str:
        """Return a URL that external services (e.g. HaulSafe OCR) can reach.

        For S3 this produces a presigned URL.
        For local storage this requires LOCAL_STORAGE_BASE_URL to be configured.
        Raises StorageError if the implementation cannot produce a public URL.
        """
        ...
