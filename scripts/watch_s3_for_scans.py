"""Continuously watch the S3 bucket for new LCPT scan PDFs and process them.

Polls UploadedFromSharedcifs/ every N seconds, filters out the noise (acetera /
Pepsi which is not in the cover-sheet pilot, plus FedEx receipts and tiny
files), and runs each new PDF through the Mode 3 pipeline.

Idempotency comes from the pipeline's existing SQLite store — restarting the
watcher won't reprocess files that already succeeded.

Usage:
    python scripts/watch_s3_for_scans.py
    python scripts/watch_s3_for_scans.py --interval 30
    python scripts/watch_s3_for_scans.py --once
    python scripts/watch_s3_for_scans.py --include cpegg lmcnair
    python scripts/watch_s3_for_scans.py --include cpegg lmcnair --once
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Make src/ importable when running this script directly
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))


# Clients we never auto-process (per Levi: acetera = Pepsi, not in the pilot)
# Skip + include lists are loaded from settings (.env keys
# LCPT_SKIP_USERS and LCPT_INCLUDE_USERS). Hardcoded fallbacks below
# in case .env is missing the keys.
_FALLBACK_SKIP = {"acetera", "bmyers", "cwilt", "klemke", "wmcneese"}


def _load_user_set(value: str, fallback: set) -> set:
    """Parse comma-separated user list from settings (returns lowercase set)."""
    if not value:
        return fallback
    return {u.strip().lower() for u in value.split(",") if u.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bucket", default=None,
                        help="S3 bucket (defaults to LCPT_SCAN_BUCKET in .env)")
    parser.add_argument("--prefix", default="UploadedFromSharedcifs/",
                        help="S3 prefix to watch")
    parser.add_argument("--interval", type=int, default=60,
                        help="Poll interval in seconds (default: 60)")
    parser.add_argument("--once", action="store_true",
                        help="Single pass instead of continuous loop")
    parser.add_argument("--include", nargs="*", default=None,
                        help="Only process files under these client/rep folders. "
                             "Example: --include cpegg lmcnair")
    parser.add_argument("--min-size", type=int, default=100_000,
                        help="Skip files smaller than this many bytes (default: 100KB)")
    parser.add_argument(
        "--mock-cp", dest="mock_cp", action="store_true", default=True,
        help="Use mocked CP Suite client (Mode 3). DEFAULT.",
    )
    parser.add_argument(
        "--real-cp", dest="mock_cp", action="store_false",
        help="Use real CP Suite client (Mode 4 — full end-to-end)",
    )
    args = parser.parse_args()

    from lcpt_scan_automation.config.settings import Settings, configure_logging
    from lcpt_scan_automation.domain.models import ScanEvent
    from lcpt_scan_automation.entrypoints.container import build_process_scan_use_case
    from lcpt_scan_automation.infrastructure.storage.s3_storage import S3Storage

    settings = Settings()
    configure_logging(settings)
    bucket = args.bucket or settings.lcpt_scan_bucket

    storage = S3Storage(
        bucket=bucket,
        region=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id or None,
        aws_secret_access_key=settings.aws_secret_access_key or None,
    )

    settings_for_use = settings.model_copy(update={"lcpt_scan_bucket": bucket})
    use_case = build_process_scan_use_case(
        settings_for_use,
        use_mock_cp=args.mock_cp,
        use_s3=True,
        s3_client=storage._client,
    )

    mode = "Mode 3 (mock CP)" if args.mock_cp else "Mode 4 (real CP)"

    skip_set = _load_user_set(settings.lcpt_skip_users, _FALLBACK_SKIP)
    settings_include = _load_user_set(settings.lcpt_include_users, set())
    include_set = {c.lower() for c in args.include} if args.include else None
    if include_set is None and settings_include:
        include_set = settings_include
    include_str = ", ".join(sorted(include_set)) if include_set else "all reps (except skip list)"
    skip_str = ", ".join(sorted(skip_set)) or "(none)"

    print(f"Watching s3://{bucket}/{args.prefix}")
    print(f"  Mode         : {mode}")
    print(f"  Include reps : {include_str}")
    print(f"  Skip clients : {skip_str}")
    print(f"  Min size     : {args.min_size:,} bytes")
    print(f"  Interval     : {args.interval}s "
          f"{'(single pass)' if args.once else '(loop forever — Ctrl+C to stop)'}")
    print()

    iteration = 0

    while True:
        iteration += 1
        print(f"── Pass {iteration} ──────────────────────────────────────────────────")

        # When we have an include list, list each rep's folder individually so we
        # don't get stuck behind alphabetically-earlier reps when bucket has >1000
        # objects (S3 list_objects caps at 1000 per call without pagination).
        try:
            if include_set:
                objs = []
                for rep in sorted(include_set):
                    rep_prefix = args.prefix.rstrip("/") + "/" + rep + "/"
                    rep_objs = storage.list_objects(prefix=rep_prefix, max_keys=1000)
                    objs.extend(rep_objs)
            else:
                objs = storage.list_objects(prefix=args.prefix, max_keys=1000)
        except Exception as exc:
            print(f"  ERROR listing S3: {exc}", file=sys.stderr)
            if args.once:
                return 1
            time.sleep(args.interval)
            continue

        candidates = _filter_candidates(objs, include_set, args.min_size, skip_set)
        print(f"  Found {len(candidates)} candidate(s) after filtering "
              f"(of {len(objs)} total)")

        processed = 0
        skipped_dup = 0
        errored = 0

        for o in candidates:
            try:
                event = ScanEvent(source_path=o.key, etag=o.etag or "")
                record = use_case.execute(event)
                state = str(record.state)
                if state == "SUCCESS" and record.created_at != record.updated_at:
                    skipped_dup += 1  # rough proxy for "already processed"
                    print(f"  - duplicate  {o.key}  ({state})")
                else:
                    processed += 1
                    print(f"  + {state:<18}  {o.key}  (scan_id={record.scan_id[:12]}...)")
            except Exception as exc:
                errored += 1
                print(f"  ! ERROR  {o.key}: {exc}", file=sys.stderr)

        print(f"  Summary  processed={processed}  duplicates={skipped_dup}  errors={errored}")
        print()

        if args.once:
            break

        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nInterrupted — exiting cleanly.")
            break

    return 0


def _filter_candidates(objs, include_set, min_size, skip_set):
    """Filter the raw S3 listing to actual scan candidates."""
    candidates = []
    for o in objs:
        if not o.key.lower().endswith(".pdf"):
            continue
        if (o.size or 0) < min_size:
            continue
        if "fedex receipts" in o.key.lower():
            continue

        # Extract the client/rep folder (second segment of the path)
        parts = o.key.split("/")
        if len(parts) < 3 or not parts[1]:
            continue
        client = parts[1].lower()

        if client in skip_set:
            continue
        if include_set is not None and client not in include_set:
            continue

        candidates.append(o)

    # Sort oldest first so we process in roughly upload order
    candidates.sort(key=lambda o: o.last_modified or "")
    return candidates


if __name__ == "__main__":
    sys.exit(main())
