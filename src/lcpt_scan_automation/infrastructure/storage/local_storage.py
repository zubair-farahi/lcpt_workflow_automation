import hashlib
from pathlib import Path
from typing import Optional

from ...domain.errors import StorageError


def compute_file_hash(path: str | Path) -> str:
    """Return SHA-256 hex digest of file contents — used as local idempotency ETag."""
    p = Path(path)
    if not p.exists():
        raise StorageError(f"Cannot hash file, not found: {p}")
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class LocalStorage:
    """File-system storage adapter for local development.

    generate_accessible_url() requires LOCAL_STORAGE_BASE_URL to be set
    because HaulSafe OCR must reach the document over HTTP.
    Use an ngrok tunnel or similar during local development, OR use S3Storage
    which provides presigned URLs automatically.
    """

    def __init__(
        self,
        base_dir: str | Path = "./data",
        base_url: Optional[str] = None,
    ) -> None:
        self._base = Path(base_dir)
        self._base_url = (base_url or "").rstrip("/")

    def read_bytes(self, path: str) -> bytes:
        full = self._resolve(path)
        if not full.exists():
            raise StorageError(f"File not found: {full}")
        return full.read_bytes()

    def write_bytes(self, path: str, data: bytes) -> None:
        full = self._base / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def generate_accessible_url(self, path: str, expires_in_seconds: int = 3600) -> str:
        if not self._base_url:
            raise StorageError(
                "LOCAL_STORAGE_BASE_URL is not configured. "
                "HaulSafe OCR needs a publicly reachable URL. "
                "Set LOCAL_STORAGE_BASE_URL (e.g. an ngrok tunnel) or use S3Storage."
            )
        filename = Path(path).name
        return f"{self._base_url}/{filename}"

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self._base / path
