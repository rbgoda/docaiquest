from typing import Dict, List, Optional
from datetime import datetime
import logging
import re

from .quality_scorer import QualityScorer

logger = logging.getLogger(__name__)


class AnomalyDetector:
    """Detect unusual patterns in extracted data"""

    def __init__(self):
        self.logger = logger

    async def detect(self, fields: Dict, extraction_results: Dict) -> List[Dict]:
        """Detect anomalies in extracted data"""

        anomalies = []

        # 1. Missing Required Fields
        required_fields = self._get_required_fields_for_doc_type(
            fields.get("document_type", "")
        )

        for field in required_fields:
            if not fields.get(field):
                anomalies.append(
                    {
                        "type": "missing_required_field",
                        "field": field,
                        "severity": "high",
                        "message": f"{field} missing for {fields.get('document_type')} document",
                        "suggestion": f"Check PDF for {field}",
                    }
                )

        # 2. Out-of-Range Values
        total_due = self._parse_amount(fields.get("total_due", "0"))

        if total_due == 0:
            anomalies.append(
                {
                    "type": "zero_amount",
                    "field": "total_due",
                    "severity": "medium",
                    "message": "Total due is zero or empty",
                    "suggestion": "Verify amount extraction",
                }
            )

        if total_due > 1000000:
            anomalies.append(
                {
                    "type": "unusually_large_amount",
                    "field": "total_due",
                    "severity": "medium",
                    "message": f"Very large amount: ${total_due:,.2f}",
                    "suggestion": "Verify amount is not misread",
                }
            )

        # 3. Date Anomalies
        date_val = self._parse_date(fields.get("date", ""))

        if date_val and date_val > datetime.now():
            anomalies.append(
                {
                    "type": "future_date",
                    "field": "date",
                    "severity": "high",
                    "message": f"Date is in future: {date_val.strftime('%m/%d/%Y')}",
                    "suggestion": "Check PDF - likely extraction error",
                }
            )

        if date_val and (datetime.now() - date_val).days > 1000:
            anomalies.append(
                {
                    "type": "very_old_date",
                    "field": "date",
                    "severity": "low",
                    "message": f"Document is very old: {(datetime.now() - date_val).days} days",
                    "suggestion": "Verify this is the correct document",
                }
            )

        # 4. Line Items Consistency
        line_items = fields.get("line_items", [])

        if line_items and total_due == 0:
            anomalies.append(
                {
                    "type": "line_items_but_no_total",
                    "field": "line_items",
                    "severity": "high",
                    "message": "Has line items but no total amount",
                    "suggestion": "Total may be on different page",
                }
            )

        if line_items:
            if isinstance(line_items, str):
                line_items = [line_items]

            line_sum = sum(self._parse_amount(item) for item in line_items)

            if line_sum > 0 and total_due > 0:
                diff_percent = abs(total_due - line_sum) / max(line_sum, total_due)

                if diff_percent > 0.10:
                    anomalies.append(
                        {
                            "type": "amount_mismatch",
                            "field": "line_items",
                            "severity": "high",
                            "message": f"Line items sum (${line_sum:,.2f}) doesn't match total (${total_due:,.2f})",
                            "suggestion": "Check for missing items or calculation errors",
                        }
                    )

        # 5. Sparse PDF Warnings
        sparsity = extraction_results.get("_metadata", {}).get("sparsity", 0.0)

        if sparsity > 0.7:
            anomalies.append(
                {
                    "type": "sparse_pdf",
                    "severity": "medium",
                    "message": "PDF is heavily scanned - text may have errors",
                    "suggestion": "Review extracted fields carefully",
                }
            )

        return anomalies

    @staticmethod
    def _get_required_fields_for_doc_type(doc_type: str) -> list:
        """Return required fields based on document type"""
        requirements = {
            "invoice": ["invoice_number", "date", "total_due"],
            "receipt": ["date", "total_due"],
            "statement": ["date"],
        }
        return requirements.get(doc_type.lower(), [])

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


class QualityDetector:
    """
    Main quality detection service.

    Orchestrates:
    - Quality scoring (6 dimensions per field)
    - Anomaly detection
    - Risk aggregation
    """

    def __init__(self):
        self.scorer = QualityScorer()
        self.anomaly_detector = AnomalyDetector()

    async def detect_quality(
        self, extracted_fields: Dict, extraction_results: Dict
    ) -> Dict:
        """
        Detect quality for entire extraction.

        Args:
            extracted_fields: All fields with values
            extraction_results: Extraction output (with handler results)

        Returns:
        {
            "overall_quality": 0.82,
            "quality_level": "good",
            "field_scores": {...},
            "anomalies": [...],
            "fields_needing_review": [...],
            "highest_risk_fields": [...]
        }
        """

        field_scores = {}

        for field_name, field_value in extracted_fields.items():
            if not field_value:
                continue

            field_data = extraction_results.get(
                field_name, {"value": field_value, "confidence": 0.75}
            )

            field_quality = await self.scorer.score_field(
                field_name, field_data, extracted_fields, extraction_results
            )
            field_scores[field_name] = field_quality

        anomalies = await self.anomaly_detector.detect(extracted_fields, extraction_results)

        overall_quality = self._aggregate_quality(field_scores)

        fields_needing_review = [
            field
            for field, score in field_scores.items()
            if score["risk_level"] in ["high", "medium"]
        ]

        highest_risk = sorted(
            [
                {
                    "field": field,
                    "risk": score["risk_level"],
                    "confidence": score["confidence"],
                    "why": (
                        score["confidence_reasons"][0]
                        if score["confidence_reasons"]
                        else "Low confidence"
                    ),
                }
                for field, score in field_scores.items()
            ],
            key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["risk"]],
        )

        return {
            "overall_quality": overall_quality,
            "quality_level": self._quality_level_from_score(overall_quality),
            "field_scores": field_scores,
            "anomalies": anomalies,
            "fields_needing_review": fields_needing_review,
            "highest_risk_fields": highest_risk[:3],
            "timestamp": datetime.now().isoformat(),
        }

    @staticmethod
    def _aggregate_quality(field_scores: Dict) -> float:
        """Combine all field scores into overall quality"""

        if not field_scores:
            return 0.5

        field_weights = {
            "invoice_number": 1.0,
            "date": 1.0,
            "total_due": 1.0,
            "vendor": 0.8,
            "line_items": 0.9,
            "description": 0.7,
            "document_type": 0.7,
            "category": 0.6,
            "risk_level": 0.6,
        }

        total_weight = 0
        weighted_sum = 0

        for field, score in field_scores.items():
            weight = field_weights.get(field, 0.7)
            weighted_sum += score["confidence"] * weight
            total_weight += weight

        return weighted_sum / total_weight if total_weight > 0 else 0.5

    @staticmethod
    def _quality_level_from_score(score: float) -> str:
        """Convert score to quality level"""

        if score >= 0.85:
            return "good"
        elif score >= 0.70:
            return "fair"
        else:
            return "poor"
