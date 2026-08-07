from typing import Dict, Optional
import logging
from datetime import datetime
import re

logger = logging.getLogger(__name__)

try:
    from fuzzywuzzy import fuzz
except ImportError:
    fuzz = None
    logger.warning("fuzzywuzzy not installed — fuzzy field matching disabled")


class QualityScorer:
    """
    Score field quality across 6 dimensions.

    Dimensions:
    1. LLM Confidence (25%)
    2. Format Validity (20%)
    3. Data Consistency (15%)
    4. Positional Validity (15%)
    5. Field Uniqueness (10%)
    6. Text Clarity (15%)
    """

    WEIGHTS = {
        "llm_confidence": 0.25,
        "format_validity": 0.20,
        "consistency": 0.15,
        "positional_validity": 0.15,
        "uniqueness": 0.10,
        "text_clarity": 0.15,
    }

    EXPECTED_POSITIONS = {
        "invoice_number": (0.0, 0.0, 1.0, 0.15),
        "date": (0.0, 0.0, 1.0, 0.20),
        "vendor": (0.0, 0.0, 1.0, 0.25),
        "line_items": (0.0, 0.20, 1.0, 0.70),
        "description": (0.0, 0.20, 1.0, 0.75),
        "total_due": (0.0, 0.75, 1.0, 1.0),
    }

    FORMATS = {
        "invoice_number": r"^[A-Z0-9\-/]{3,20}$",
        "date": r"^\d{1,2}/\d{1,2}/\d{2,4}$|^[A-Z][a-z]{2} \d{1,2}, \d{4}$",
        "total_due": r"^\$?[\d,]+\.\d{2}$",
        "vendor": r"^[A-Za-z0-9\s\.,&'-]{3,100}$",
    }

    def __init__(self):
        self.logger = logger

    async def score_field(
        self,
        field_name: str,
        field_data: Dict,
        all_fields: Dict,
        extraction_results: Dict,
    ) -> Dict:
        """
        Calculate comprehensive quality score for a field.

        Returns:
        {
            "confidence": 0.85,
            "dimensions": {...},
            "quality_flags": [...],
            "risk_level": "low",
            "confidence_reasons": [...]
        }
        """

        scores = {}

        # 1. LLM Confidence (25%)
        base_llm = field_data.get("confidence", 0.75)
        scores["llm_confidence"] = base_llm

        # 2. Format Validity (20%)
        is_valid, format_conf = self._validate_format(
            field_name, field_data.get("value", "")
        )
        scores["format_validity"] = format_conf

        # 3. Consistency (15%)
        consistency_adj = self._check_consistency(all_fields, field_name)
        scores["consistency"] = max(0, min(1, base_llm + consistency_adj))

        # 4. Positional Validity (15%)
        if field_data.get("bbox"):
            pos_adj = self._validate_position(field_name, field_data["bbox"])
            scores["positional_validity"] = max(0, min(1, base_llm + pos_adj))
        else:
            scores["positional_validity"] = 0.7  # Neutral for classifier fields

        # 5. Uniqueness (10%)
        unique_adj = self._check_uniqueness(all_fields, field_name)
        scores["uniqueness"] = max(0, min(1, base_llm + unique_adj))

        # 6. Text Clarity (15%)
        clarity_adj = self._check_clarity(extraction_results, field_name)
        scores["text_clarity"] = max(0, min(1, base_llm + clarity_adj))

        # Combine with weights
        final_confidence = sum(
            scores[dim] * self.WEIGHTS[dim] for dim in self.WEIGHTS
        )

        quality_flags = self._determine_flags(scores, final_confidence)
        risk_level = self._determine_risk_level(final_confidence)
        reasons = self._generate_reasons(scores, quality_flags)

        return {
            "confidence": min(0.99, final_confidence),
            "dimensions": scores,
            "quality_flags": quality_flags,
            "risk_level": risk_level,
            "confidence_reasons": reasons,
        }

    def _validate_format(self, field_name: str, value: str) -> tuple:
        """Validate field format matches expected pattern"""

        if field_name not in self.FORMATS:
            return True, 0.8

        pattern = self.FORMATS[field_name]

        try:
            if re.match(pattern, str(value), re.IGNORECASE):
                return True, 1.0
        except Exception:
            pass

        if fuzz:
            similarity = fuzz.ratio(str(value).lower(), pattern.lower())
            if similarity > 0.85:
                return True, 0.85
            elif similarity > 0.70:
                return False, 0.60
            else:
                return False, 0.30
        else:
            return len(str(value)) > 0, 0.7

    def _check_consistency(self, fields: Dict, field_name: str) -> float:
        """Check if field makes sense given other fields"""

        if field_name == "total_due":
            line_items = fields.get("line_items", [])
            total_str = fields.get("total_due", "0")
            total = self._parse_amount(str(total_str))

            if isinstance(line_items, (list, str)):
                if isinstance(line_items, str):
                    line_items = [line_items]

                line_sum = sum(self._parse_amount(item) for item in line_items)

                if total >= line_sum:
                    return +0.10
                else:
                    return -0.15

        elif field_name == "date":
            date_val = self._parse_date(fields.get("date", ""))
            if date_val and date_val <= datetime.now():
                return +0.05
            elif date_val:
                return -0.20

        return 0.0

    def _validate_position(self, field_name: str, bbox: Dict) -> float:
        """Check if field bbox is in expected region"""

        if field_name not in self.EXPECTED_POSITIONS:
            return 0.0

        expected = self.EXPECTED_POSITIONS[field_name]

        if self._overlaps(bbox, expected):
            overlap_ratio = self._calculate_overlap(bbox, expected)
            if overlap_ratio > 0.8:
                return +0.10
            elif overlap_ratio > 0.5:
                return +0.05
            else:
                return -0.05
        else:
            return -0.15

    def _check_uniqueness(self, fields: Dict, field_name: str) -> float:
        """Check if value is unique across fields"""

        value = fields.get(field_name, "")
        duplicates = 0

        for other_field, other_value in fields.items():
            if other_field == field_name or not isinstance(other_value, str):
                continue

            if self._similar(str(value), str(other_value)):
                duplicates += 1

        if duplicates == 0:
            return +0.05
        else:
            return -0.10

    def _check_clarity(self, extraction_results: Dict, field_name: str) -> float:
        """Check text clarity from extraction metrics"""

        if "ocr_confidence" in extraction_results.get(field_name, {}):
            ocr_conf = extraction_results[field_name]["ocr_confidence"]

            if ocr_conf > 0.95:
                return +0.10
            elif ocr_conf > 0.85:
                return +0.05
            elif ocr_conf > 0.70:
                return 0.0
            else:
                return -0.15

        sparsity = extraction_results.get("_metadata", {}).get("sparsity", 0.0)

        if sparsity > 0.7:
            return -0.20
        elif sparsity > 0.3:
            return -0.10
        else:
            return 0.0

    def _determine_flags(self, scores: Dict, confidence: float) -> list:
        """Determine quality flags"""

        flags = []

        if scores["format_validity"] < 0.70:
            flags.append("format_invalid")

        if scores["consistency"] < 0.60:
            flags.append("inconsistent_with_other_fields")

        if scores["positional_validity"] < 0.70:
            flags.append("unexpected_position")

        if scores["text_clarity"] < 0.70:
            flags.append("low_text_clarity")

        if confidence < 0.70:
            flags.append("low_overall_confidence")

        return flags

    def _determine_risk_level(self, confidence: float) -> str:
        """Determine risk level from confidence"""

        if confidence >= 0.85:
            return "low"
        elif confidence >= 0.70:
            return "medium"
        else:
            return "high"

    def _generate_reasons(self, scores: Dict, flags: list) -> list:
        """Generate human-readable confidence reasons"""

        reasons = []

        if scores["llm_confidence"] > 0.85:
            reasons.append("Strong LLM extraction confidence")

        if scores["format_validity"] == 1.0:
            reasons.append("Perfect format match")

        if scores["consistency"] > 0.85:
            reasons.append("Consistent with other fields")

        if scores["text_clarity"] > 0.85:
            reasons.append("Clear text in PDF")

        for flag in flags:
            if flag == "format_invalid":
                reasons.append("Format doesn't match expected pattern")
            elif flag == "inconsistent_with_other_fields":
                reasons.append("Value inconsistent with other fields")
            elif flag == "low_text_clarity":
                reasons.append("Text clarity issues in PDF")

        return reasons

    @staticmethod
    def _overlaps(bbox1: Dict, region: tuple) -> bool:
        """Check if two bboxes overlap"""
        return (
            bbox1.get("x0", 0) < region[2]
            and bbox1.get("x1", 1) > region[0]
            and bbox1.get("y0", 0) < region[3]
            and bbox1.get("y1", 1) > region[1]
        )

    @staticmethod
    def _calculate_overlap(bbox: Dict, region: tuple) -> float:
        """Calculate overlap percentage"""
        overlap_x = min(bbox.get("x1", 1), region[2]) - max(
            bbox.get("x0", 0), region[0]
        )
        overlap_y = min(bbox.get("y1", 1), region[3]) - max(
            bbox.get("y0", 0), region[1]
        )

        if overlap_x <= 0 or overlap_y <= 0:
            return 0.0

        overlap_area = overlap_x * overlap_y
        bbox_area = (bbox.get("x1", 1) - bbox.get("x0", 0)) * (
            bbox.get("y1", 1) - bbox.get("y0", 0)
        )

        return min(1.0, overlap_area / max(bbox_area, 0.0001))

    @staticmethod
    def _similar(val1: str, val2: str) -> bool:
        """Check if values are similar"""
        if fuzz:
            return fuzz.ratio(val1.lower(), val2.lower()) > 0.85
        else:
            return val1.lower() == val2.lower()

    @staticmethod
    def _parse_amount(amount_str: str) -> float:
        """Parse amount string to float"""
        try:
            match = re.search(r"[\d,]+\.?\d*", str(amount_str).replace(",", ""))
            if match:
                return float(match.group())
        except (ValueError, AttributeError):
            pass
        return 0.0

    @staticmethod
    def _parse_date(date_str: str) -> Optional[datetime]:
        """Parse date string"""
        for fmt in ["%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"]:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None
