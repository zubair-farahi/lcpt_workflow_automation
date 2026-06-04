import io

from pypdf import PdfReader, PdfWriter

from ...domain.errors import PdfProcessingError
from ...ports.pdf_processor import PdfSplit


class PypdfProcessor:
    """Splits a scanned PDF into cover sheet (page 1) and packet (pages 2+)."""

    def split_cover_and_packet(self, pdf_bytes: bytes) -> PdfSplit:
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
        except Exception as exc:
            raise PdfProcessingError(f"Failed to read PDF: {exc}") from exc

        total = len(reader.pages)

        cover_bytes = self._extract_pages(reader, [0])

        if total == 1:
            # Caller must check and route to review; return empty packet
            return PdfSplit(
                cover_sheet_bytes=cover_bytes,
                packet_bytes=b"",
                total_pages=total,
            )

        packet_bytes = self._extract_pages(reader, list(range(1, total)))
        return PdfSplit(
            cover_sheet_bytes=cover_bytes,
            packet_bytes=packet_bytes,
            total_pages=total,
        )

    @staticmethod
    def _extract_pages(reader: PdfReader, page_indices: list[int]) -> bytes:
        writer = PdfWriter()
        for idx in page_indices:
            writer.add_page(reader.pages[idx])
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()
