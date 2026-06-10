import io

from pypdf import PdfReader, PdfWriter

from ...domain.errors import PdfProcessingError
from ...ports.pdf_processor import PdfSplit


class PypdfProcessor:
    """Splits a scanned PDF into cover sheet (page 1) and packet (pages 2+).

    Also offers PDF -> PNG rendering of the cover sheet so OCR services that
    can't ingest PDFs (like HaulSafe's vision AI which currently 500s on direct
    PDF input) get an image instead.
    """

    def split_cover_and_packet(self, pdf_bytes: bytes) -> PdfSplit:
        try:
            reader = PdfReader(io.BytesIO(pdf_bytes))
        except Exception as exc:
            raise PdfProcessingError(f"Failed to read PDF: {exc}") from exc

        total = len(reader.pages)

        cover_bytes = self._extract_pages(reader, [0])

        if total == 1:
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

    def render_pdf_to_png(self, pdf_bytes: bytes, dpi: int = 200) -> bytes:
        """Render page 0 of the given PDF as a PNG image.

        Used to give HaulSafe OCR an image instead of a PDF. AcroForm widget
        values (text, checkboxes) are flattened into the image via pypdfium2.
        """
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise PdfProcessingError(
                "pypdfium2 is required for render_pdf_to_png(). "
                "Install with: pip install pypdfium2"
            ) from exc

        try:
            doc = pdfium.PdfDocument(pdf_bytes)
            try:
                doc.init_forms()
            except Exception:
                pass
            page = doc[0]
            try:
                page.flatten()
            except RuntimeError:
                pass
            bitmap = page.render(scale=dpi / 72)
            pil_image = bitmap.to_pil()
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
        except Exception as exc:
            raise PdfProcessingError(f"Failed to render PDF page 0 to PNG: {exc}") from exc
