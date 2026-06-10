from typing import NamedTuple, Protocol, runtime_checkable


class PdfSplit(NamedTuple):
    cover_sheet_bytes: bytes  # page 1 only
    packet_bytes: bytes       # pages 2..N
    total_pages: int


@runtime_checkable
class PdfProcessorPort(Protocol):
    def split_cover_and_packet(self, pdf_bytes: bytes) -> PdfSplit:
        """Split a scanned PDF into its cover sheet (page 1) and packet (pages 2+).

        Raises PdfProcessingError on corrupt or unreadable input.
        Callers must check total_pages == 1 and route to review if so.
        The original bytes are never modified.
        """
        ...

    def render_pdf_to_png(self, pdf_bytes: bytes, dpi: int = 200) -> bytes:
        """Render page 0 of the PDF as a PNG image (form fields flattened).

        Used because the OCR service cannot ingest PDFs directly.
        Raises PdfProcessingError if rendering fails.
        """
        ...
