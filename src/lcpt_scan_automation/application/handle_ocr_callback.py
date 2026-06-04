"""HandleOcrCallbackUseCase — thin entry point for the async webhook path.

In the current staging implementation OCR uses polling, so this use case is
not the primary flow.  It exists so that when HaulSafe confirms webhook support
the entrypoint (webhook_api.py) can call this directly without changing
ProcessScanUseCase.
"""

from ..domain.models import OcrResult, ScanRecord
from .process_scan import ProcessScanUseCase


class HandleOcrCallbackUseCase:
    def __init__(self, process_scan: ProcessScanUseCase) -> None:
        self._process_scan = process_scan

    def execute(self, ocr_result: OcrResult) -> ScanRecord:
        """Continue a scan that was left in AWAITING_OCR state."""
        return self._process_scan.continue_from_ocr_result(ocr_result)
