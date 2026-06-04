"""Creates a minimal 2-page sample scan PDF for local testing.

Page 1  — cover sheet placeholder (white page)
Page 2+ — packet pages (white pages)

Usage:
    python samples/create_sample_pdf.py
    python samples/create_sample_pdf.py --pages 3 --out samples/scan_3page.pdf
"""

import argparse
import io
from pathlib import Path

from pypdf import PdfWriter


def make_sample_pdf(num_pages: int = 2) -> bytes:
    writer = PdfWriter()
    for i in range(num_pages):
        page = writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a sample scan PDF for local testing.")
    parser.add_argument("--pages", type=int, default=2, help="Total number of pages (default: 2)")
    parser.add_argument("--out", default="samples/scan.pdf", help="Output file path")
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(make_sample_pdf(args.pages))
    print(f"Created {out}  ({args.pages} pages)")
