from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..domain.models import OcrField


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── CP Suite ──────────────────────────────────────────────────────────────
    # TaskManager API base (staging). Endpoints are under /api/...
    cp_suite_base_url: str = "https://cp3-staging.itscomply.com/task-manager-api"
    # OAuth2 password-grant identity server
    cp_suite_identity_server: str = "https://stg-id.itscomply.com"
    cp_suite_grant_type: str = "password"
    cp_suite_client_id: str = "cp3Client"
    cp_suite_client_secret: str = ""
    cp_suite_username: str = ""
    cp_suite_password: str = ""
    cp_suite_timeout_seconds: float = 30.0
    # Refresh the token this many seconds before it actually expires
    cp_suite_token_refresh_margin_seconds: int = 30
    # Attachment endpoint params (CONFIRM with CP Suite — see cp_suite_http_client TODOs)
    cp_suite_file_category: str = "Document"
    # Optional explicit user id for notes/attachments (server may infer from token)
    cp_suite_user_id: str = ""

    # ── HaulSafe OCR ──────────────────────────────────────────────────────────
    haul_ocr_base_url: str = "https://haul-safe-document-api-staging.haulwith.us"
    haul_ocr_api_key: str = ""
    haul_ocr_poll_interval_seconds: float = 5.0
    haul_ocr_max_wait_seconds: float = 120.0
    haul_ocr_max_attempts: int = 24
    require_ocr_confidence: bool = False
    ocr_confidence_threshold: float = 0.7

    # ── AWS / S3 ──────────────────────────────────────────────────────────────
    aws_region: str = "us-east-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    lcpt_scan_bucket: str = "fw-ocr-project"

    # S3 prefix configuration — make sure all end with "/"
    lcpt_scan_incoming_prefix: str = "incoming/"
    lcpt_scan_processing_prefix: str = "processing/"
    lcpt_scan_processed_prefix: str = "processed/"
    lcpt_scan_review_prefix: str = "review/"
    lcpt_scan_failed_prefix: str = "failed/"

    s3_presigned_url_expiry_seconds: int = 900

    # ── Local storage ─────────────────────────────────────────────────────────
    local_storage_dir: str = "./data"
    local_storage_base_url: str = ""

    # ── Review queue ──────────────────────────────────────────────────────────
    review_queue_dir: str = "./review_queue"

    # ── Processing policies ───────────────────────────────────────────────────
    missing_checklist_item_policy: str = "review"
    company_prefix_validation_required: bool = False
    wr_number_pattern: str = r"^[A-Z]{2,6}-WR-\d+$"

    # ── Config file paths ─────────────────────────────────────────────────────
    checklist_mapping_path: str = "./config/checklist_mapping.yaml"
    company_prefix_mapping_path: str = "./config/company_prefix_mapping.yaml"
    haul_ocr_fields_path: str = "./config/haul_ocr_fields.yaml"

    # ── Logging ───────────────────────────────────────────────────────────────
    json_logs: bool = False
    log_level: str = "INFO"

    # ── Derived loaders (not env vars) ────────────────────────────────────────

    def load_ocr_fields(self) -> list[OcrField]:
        path = Path(self.haul_ocr_fields_path)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return [
            OcrField(field_name=f["fieldName"], field_type=f["fieldType"])
            for f in data.get("fields", [])
        ]

    def load_company_prefix_mapping(self) -> dict[str, list[str]]:
        path = Path(self.company_prefix_mapping_path)
        if not path.exists():
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data.get("prefix_to_companies", {})

    def build_s3_client(self):
        """Build a boto3 S3 client using configured credentials."""
        import boto3

        kwargs: dict[str, Any] = {"region_name": self.aws_region}
        if self.aws_access_key_id and self.aws_secret_access_key:
            kwargs["aws_access_key_id"] = self.aws_access_key_id
            kwargs["aws_secret_access_key"] = self.aws_secret_access_key
        return boto3.client("s3", **kwargs)

    def build_sts_client(self):
        """Build a boto3 STS client using configured credentials."""
        import boto3

        kwargs: dict[str, Any] = {"region_name": self.aws_region}
        if self.aws_access_key_id and self.aws_secret_access_key:
            kwargs["aws_access_key_id"] = self.aws_access_key_id
            kwargs["aws_secret_access_key"] = self.aws_secret_access_key
        return boto3.client("sts", **kwargs)


def configure_logging(settings: Settings) -> None:
    """Configure structlog for either human-readable (dev) or JSON (prod/Lambda) output."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level)

    # Use structlog.processors.add_log_level (not the stdlib variant) so this
    # works with PrintLoggerFactory.  The stdlib variant requires a logger with
    # a .name attribute which PrintLogger does not have.
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
    ]

    if settings.json_logs:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
