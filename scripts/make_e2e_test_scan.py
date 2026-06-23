"""Create a realistic FILLED cover-sheet scan and upload it to S3.

This simulates what a rep produces: page 1 = filled LCPT cover sheet,
pages 2..N = the packet. Values target the SAFE staging WR (STC-WR-154).

Usage:
    python scripts/make_e2e_test_scan.py            # build + upload
    python scripts/make_e2e_test_scan.py --no-upload  # build only

Then process it end-to-end:
    python -m lcpt_scan_automation.entrypoints.local_cli process-s3 \
        --key <printed-key>
"""

from __future__ import annotations

import argparse
import io
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def build_scan_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    w, h = letter

    # ── Page 1: filled cover sheet ──
    y = h - 70
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(w / 2, y, "COVER SHEET")
    y -= 50
    c.setFont("Helvetica", 12)
    c.drawString(72, y, "Company Name:  Smoke Test Client")
    y -= 28
    c.drawString(72, y, "Work Request Number:  STC-WR-154")
    y -= 40
    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, y, "Routing")
    y -= 24
    c.setFont("Helvetica", 12)
    c.drawString(90, y, "[X]  Attach documents to Internal Attachments")
    y -= 22
    c.drawString(90, y, "[  ]  Attach documents to Attachments")
    y -= 40
    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, y, "Checklist")
    y -= 24
    c.setFont("Helvetica", 12)
    c.drawString(90, y, "[  ]  Process Through State Agency")
    y -= 22
    c.drawString(90, y, "[X]  Receive Credentials")
    y -= 22
    c.drawString(90, y, "[  ]  Complete")
    y -= 40
    c.drawString(72, y, "Additional Notes:  E2E pipeline demo - safe to ignore")
    y -= 28
    c.drawString(72, y, "Completed By:  Zubair Farahi")
    y -= 28
    c.drawString(72, y, "Date:  2026-06-09")
    c.showPage()

    # ── Pages 2-3: fake packet ──
    for i in (2, 3):
        c.setFont("Helvetica", 14)
        c.drawString(72, h - 100, f"Packet page {i} - placeholder document content")
        c.showPage()

    c.save()
    return buf.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--prefix", default="test-uploads/")
    args = parser.parse_args()

    pdf = build_scan_pdf()
    local = Path("data") / "e2e_test_scan.pdf"
    local.parent.mkdir(exist_ok=True)
    local.write_bytes(pdf)
    print(f"Built scan: {local}  ({len(pdf):,} bytes, 3 pages)")

    if args.no_upload:
        return 0

    from lcpt_scan_automation.config.settings import Settings
    from lcpt_scan_automation.infrastructure.storage.s3_storage import S3Storage

    s = Settings()
    storage = S3Storage(
        bucket=s.lcpt_scan_bucket,
        region=s.aws_region,
        aws_access_key_id=s.aws_access_key_id or None,
        aws_secret_access_key=s.aws_secret_access_key or None,
    )
    key = f"{args.prefix}e2e-demo-{uuid.uuid4().hex[:8]}.pdf"
    storage.write_bytes(key, pdf)
    print(f"Uploaded:  s3://{s.lcpt_scan_bucket}/{key}")
    print()
    print("Now run the full production pipeline on it:")
    print(f'  python -m lcpt_scan_automation.entrypoints.local_cli process-s3 --key "{key}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
