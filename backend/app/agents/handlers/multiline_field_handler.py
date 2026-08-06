from typing import Dict, List, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

try:
    from fuzzywuzzy import fuzz
except ImportError:
    logger.warning("fuzzywuzzy not installed, using basic string matching")
    fuzz = None


@dataclass
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    def to_dict(self):
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}


class MultilineFieldHandler:
    """
    Handle multi-line fields that span 2-5+ lines.

    For each multi-line field:
      1. Split extracted value by lines
      2. Find each line in Surya output using fuzzy matching
      3. Return multiple bboxes (one per line)
      4. Compute region bbox (covers all lines)
      5. Calculate confidence based on coverage
    """

    MULTILINE_FIELDS = ["line_items", "description", "terms", "footnotes"]
    FUZZY_THRESHOLD = 85

    def __init__(self):
        self.logger = logger

    async def handle(self, surya_results: Dict, extracted_fields: Dict) -> Dict:
        """
        Main entry point. Called after Surya extraction, before quality scoring.

        Args:
            surya_results: Raw Surya output with line-level bboxes
            extracted_fields: Extracted field values (including multi-line)

        Returns:
            Updated surya_results with multi-line field handling
        """

        for field_name in self.MULTILINE_FIELDS:
            if field_name not in extracted_fields:
                continue

            field_value = extracted_fields[field_name]

            if not isinstance(field_value, (list, str)) or not field_value:
                continue

            if isinstance(field_value, str):
                lines = field_value.split('\n')
            else:
                lines = field_value if isinstance(field_value, list) else [field_value]

            if len(lines) <= 1:
                continue

            bboxes = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                bbox = await self._find_line_bbox(line, surya_results)
                if bbox:
                    bboxes.append(bbox)

            if not bboxes:
                self.logger.warning(f"No bboxes found for multi-line field: {field_name}")
                continue

            region_bbox = self._compute_region_bbox(bboxes)
            coverage_ratio = len(bboxes) / len([l for l in lines if l.strip()])
            confidence = 0.96 * coverage_ratio

            surya_results[field_name] = {
                "value": field_value,
                "bboxes": [b.to_dict() for b in bboxes],
                "region_bbox": region_bbox.to_dict(),
                "confidence": min(0.96, confidence),
                "handler": "multiline",
                "line_count": len(bboxes),
            }

            self.logger.info(f"Multiline handler: {field_name} → {len(bboxes)} lines, "
                           f"confidence: {confidence:.2%}")

        return surya_results

    async def _find_line_bbox(self, line_text: str, surya_results: Dict) -> Optional[BBox]:
        """Find a specific line in Surya's output using fuzzy matching"""

        if "lines" not in surya_results:
            return None

        best_match = None
        best_score = 0

        for line_info in surya_results["lines"]:
            surya_text = line_info.get("text", "").strip()

            if not surya_text:
                continue

            if fuzz:
                score = fuzz.token_sort_ratio(line_text.lower(), surya_text.lower())
            else:
                score = self._simple_match_score(line_text.lower(), surya_text.lower())

            if score > best_score and score >= self.FUZZY_THRESHOLD:
                best_score = score
                best_match = line_info

        if best_match:
            bbox_dict = best_match.get("bbox", {})
            return BBox(
                x0=float(bbox_dict.get("x0", 0)),
                y0=float(bbox_dict.get("y0", 0)),
                x1=float(bbox_dict.get("x1", 1)),
                y1=float(bbox_dict.get("y1", 1)),
            )

        return None

    def _simple_match_score(self, text1: str, text2: str) -> float:
        """Simple string matching fallback if fuzzywuzzy not available"""
        if text1 == text2:
            return 100

        common_chars = len(set(text1) & set(text2))
        total_chars = len(set(text1) | set(text2))

        if total_chars == 0:
            return 0

        return (common_chars / total_chars) * 100

    def _compute_region_bbox(self, bboxes: List[BBox]) -> BBox:
        """Compute single bounding region covering all lines"""

        if not bboxes:
            return BBox(0, 0, 1, 1)

        return BBox(
            x0=min(b.x0 for b in bboxes),
            y0=min(b.y0 for b in bboxes),
            x1=max(b.x1 for b in bboxes),
            y1=max(b.y1 for b in bboxes),
        )
