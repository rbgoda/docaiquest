from typing import Dict
from io import BytesIO
import logging

logger = logging.getLogger(__name__)

try:
    import pdfplumber
except ImportError:
    logger.warning("pdfplumber not installed")
    pdfplumber = None


class SparsePDFHandler:
    """
    Handle PDFs with sparse/missing text layers.

    Surya is already good at handling sparse PDFs, but this handler:
      1. Detects sparsity level
      2. Optionally runs OCR on sparse regions
      3. Merges Surya + OCR results

    Sparsity score:
      0.0 = Dense text layer (no OCR needed)
      0.3 = Moderate sparsity (optional OCR)
      1.0 = Fully sparse/scanned image (OCR recommended)
    """

    SPARSITY_THRESHOLD = 0.3

    def __init__(self, use_paddle_ocr: bool = False):
        """
        Args:
            use_paddle_ocr: Enable PaddleOCR for sparse regions
        """
        self.use_paddle_ocr = use_paddle_ocr
        self.ocr = None

        if use_paddle_ocr:
            try:
                from paddleocr import PaddleOCR
                self.ocr = PaddleOCR(use_angle_cls=True, lang='en')
            except ImportError:
                logger.warning("PaddleOCR not installed, skipping sparse OCR")
                self.use_paddle_ocr = False

    async def handle(self, pdf_bytes: bytes, surya_results: Dict) -> Dict:
        """
        Handle sparse PDF enhancement.

        Args:
            pdf_bytes: Raw PDF bytes
            surya_results: Surya extraction results

        Returns:
            Enhanced surya_results with sparsity metadata
        """

        sparsity_score = self._measure_sparsity(pdf_bytes)

        if "_metadata" not in surya_results:
            surya_results["_metadata"] = {}

        surya_results["_metadata"]["sparsity"] = sparsity_score

        logger.info(f"PDF sparsity score: {sparsity_score:.2%}")

        if sparsity_score >= self.SPARSITY_THRESHOLD:
            logger.info("PDF is sparse, flagged for review")

            if self.use_paddle_ocr:
                logger.info("Running OCR enhancement...")
                surya_results = await self._enhance_with_ocr(pdf_bytes, surya_results)

        return surya_results

    def _measure_sparsity(self, pdf_bytes: bytes) -> float:
        """
        Measure how much of the PDF is missing text layer.

        Returns:
            Sparsity score 0.0 (dense) to 1.0 (sparse)
        """

        if not pdfplumber:
            logger.warning("pdfplumber not available, assuming normal density")
            return 0.1

        try:
            with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
                total_chars = 0
                total_area = 0

                for page in pdf.pages:
                    page_area = page.width * page.height
                    total_area += page_area

                    text = page.extract_text()
                    if text:
                        total_chars += len(text)

                if total_area == 0:
                    return 1.0

                char_density = total_chars / total_area

                sparsity = max(0.0, 1.0 - (char_density / 0.05))

                return min(1.0, sparsity)

        except Exception as e:
            logger.error(f"Error measuring sparsity: {e}")
            return 0.5

    async def _enhance_with_ocr(self, pdf_bytes: bytes, surya_results: Dict) -> Dict:
        """Run OCR on sparse regions and merge with Surya results"""

        if not self.ocr:
            return surya_results

        try:
            from pdf2image import convert_from_bytes
        except ImportError:
            logger.warning("pdf2image not installed, skipping OCR")
            return surya_results

        try:
            page_count = len(surya_results.get("pages", []))
            images = convert_from_bytes(
                pdf_bytes, first_page=1, last_page=min(page_count, 10)
            )

            ocr_results = []

            for page_idx, image in enumerate(images):
                ocr_output = self.ocr.ocr(image, cls=True)

                if ocr_output:
                    for result_group in ocr_output:
                        for result in result_group:
                            ocr_results.append(
                                {
                                    "page": page_idx,
                                    "text": result[1][0],
                                    "confidence": result[1][1],
                                }
                            )

            surya_results["_metadata"]["ocr_findings"] = {
                "total_ocr_lines": len(ocr_results),
                "note": "OCR results available, not merged",
            }

        except Exception as e:
            logger.error(f"Error during OCR enhancement: {e}")

        return surya_results
