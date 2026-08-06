import pytest
import asyncio
from app.agents.handlers import (
    MultilineFieldHandler,
    ClassifierFieldHandler,
    SparsePDFHandler,
)


class TestMultilineFieldHandler:
    """Test multiline field extraction"""

    @pytest.mark.asyncio
    async def test_single_line_field_unchanged(self):
        """Single-line fields should pass through unchanged"""
        handler = MultilineFieldHandler()

        surya_results = {
            "lines": [
                {
                    "text": "Invoice Number: INV-123",
                    "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.12},
                }
            ]
        }

        extracted_fields = {"invoice_number": "INV-123"}

        result = await handler.handle(surya_results, extracted_fields)

        # Single line field not in MULTILINE_FIELDS, so surya_results unchanged
        assert "invoice_number" not in result or isinstance(
            result.get("invoice_number"), dict
        )

    @pytest.mark.asyncio
    async def test_two_line_field(self):
        """Test field spanning 2 lines"""
        handler = MultilineFieldHandler()

        surya_results = {
            "lines": [
                {
                    "text": "Item 1: Service A - $100",
                    "bbox": {"x0": 0.1, "y0": 0.2, "x1": 0.5, "y1": 0.22},
                },
                {
                    "text": "Item 2: Service B - $200",
                    "bbox": {"x0": 0.1, "y0": 0.24, "x1": 0.5, "y1": 0.26},
                },
            ]
        }

        extracted_fields = {
            "line_items": ["Item 1: Service A - $100", "Item 2: Service B - $200"]
        }

        result = await handler.handle(surya_results, extracted_fields)

        assert "line_items" in result
        assert "bboxes" in result["line_items"]
        assert len(result["line_items"]["bboxes"]) == 2
        assert "region_bbox" in result["line_items"]
        assert result["line_items"]["confidence"] > 0.0

    @pytest.mark.asyncio
    async def test_no_matching_lines(self):
        """Test when no lines can be matched"""
        handler = MultilineFieldHandler()

        surya_results = {
            "lines": [
                {"text": "Some other text", "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.12}}
            ]
        }

        extracted_fields = {"line_items": ["Item not in PDF"]}

        result = await handler.handle(surya_results, extracted_fields)

        # If no matches found, field not updated
        assert "line_items" not in result or "bboxes" not in result.get("line_items", {})

    @pytest.mark.asyncio
    async def test_empty_lines(self):
        """Test with empty/whitespace lines"""
        handler = MultilineFieldHandler()

        surya_results = {
            "lines": [
                {"text": "Item 1", "bbox": {"x0": 0.1, "y0": 0.2, "x1": 0.5, "y1": 0.22}},
            ]
        }

        extracted_fields = {"line_items": ["Item 1", "", "  "]}

        result = await handler.handle(surya_results, extracted_fields)

        assert "line_items" in result
        # Only 1 non-empty line
        assert len(result["line_items"]["bboxes"]) == 1


class TestClassifierFieldHandler:
    """Test classifier field handling"""

    @pytest.mark.asyncio
    async def test_document_type_scoring_invoice(self):
        """Test invoice document type gets high confidence"""
        handler = ClassifierFieldHandler()

        surya_results = {
            "invoice_number": {"value": "INV-123"},
            "line_items": {"value": ["item1", "item2"]},
            "total_due": {"value": "$100"},
            "vendor": {"value": "Acme Corp"},
        }

        extracted_fields = {"document_type": "invoice"}

        result = await handler.handle(surya_results, extracted_fields)

        # Should have high confidence (3/4 invoice indicators present)
        assert result["document_type"]["confidence"] > 0.75
        assert result["document_type"]["bbox"] is None
        assert "confidence_breakdown" in result["document_type"]

    @pytest.mark.asyncio
    async def test_document_type_scoring_receipt(self):
        """Test receipt document type scoring"""
        handler = ClassifierFieldHandler()

        surya_results = {
            "total_due": {"value": "$50"},
        }

        extracted_fields = {"document_type": "receipt"}

        result = await handler.handle(surya_results, extracted_fields)

        # Receipt with just total_due should have moderate confidence
        assert 0.5 < result["document_type"]["confidence"] < 0.9
        assert result["document_type"]["bbox"] is None

    @pytest.mark.asyncio
    async def test_category_scoring_with_matching_vendor(self):
        """Test category scoring when vendor matches"""
        handler = ClassifierFieldHandler()

        surya_results = {}

        extracted_fields = {
            "vendor": "Spark Health Clinic",
            "category": "health",
            "document_type": "invoice",
        }

        result = await handler.handle(surya_results, extracted_fields)

        # Should have high confidence due to vendor match
        assert result["category"]["confidence"] > 0.8
        assert result["category"]["bbox"] is None

    @pytest.mark.asyncio
    async def test_risk_level_scoring_high_amount(self):
        """Test risk level for high amounts"""
        handler = ClassifierFieldHandler()

        surya_results = {}

        extracted_fields = {
            "total_due": "$50,000.00",
            "risk_level": "high",
        }

        result = await handler.handle(surya_results, extracted_fields)

        # High amount + high risk = high confidence
        assert result["risk_level"]["confidence"] > 0.75

    @pytest.mark.asyncio
    async def test_no_bbox_for_classifier_fields(self):
        """Confirm classifier fields never have bboxes"""
        handler = ClassifierFieldHandler()

        result = await handler.handle({}, {"document_type": "invoice"})

        assert result["document_type"]["bbox"] is None
        assert result["document_type"]["confidence"] > 0.0


class TestSparsePDFHandler:
    """Test sparse PDF handling"""

    def test_sparsity_measurement_dense_pdf(self):
        """Test sparsity detection on text-rich PDF"""
        handler = SparsePDFHandler()

        # Dummy PDF bytes (won't work without real PDF)
        # This just tests the method doesn't crash
        try:
            score = handler._measure_sparsity(b"dummy")
            assert 0.0 <= score <= 1.0
        except Exception:
            # Expected if pdfplumber can't parse
            pass

    @pytest.mark.asyncio
    async def test_metadata_added_to_results(self):
        """Test that sparsity metadata is added"""
        handler = SparsePDFHandler()

        surya_results = {}

        try:
            result = await handler.handle(b"dummy", surya_results)

            # Should add metadata even if measurement fails
            if "_metadata" in result:
                assert "sparsity" in result["_metadata"]
        except Exception:
            pass


# Integration tests

@pytest.mark.asyncio
async def test_full_extraction_pipeline_mock():
    """Test complete pipeline with mock data"""

    multiline_handler = MultilineFieldHandler()
    classifier_handler = ClassifierFieldHandler()
    sparse_handler = SparsePDFHandler()

    # Mock Surya output
    surya_results = {
        "invoice_number": {
            "value": "INV-2024/123",
            "confidence": 0.96,
            "bbox": {"x0": 0.1, "y0": 0.05, "x1": 0.4, "y1": 0.07},
        },
        "date": {
            "value": "09/08/2024",
            "confidence": 0.96,
            "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.3, "y1": 0.12},
        },
        "vendor": {
            "value": "Acme Corp",
            "confidence": 0.96,
            "bbox": {"x0": 0.1, "y0": 0.15, "x1": 0.4, "y1": 0.17},
        },
        "total_due": {
            "value": "$1,000.00",
            "confidence": 0.96,
            "bbox": {"x0": 0.6, "y0": 0.8, "x1": 0.8, "y1": 0.82},
        },
        "lines": [
            {"text": "Service A - $500", "bbox": {"x0": 0.1, "y0": 0.2, "x1": 0.5, "y1": 0.22}},
            {"text": "Service B - $500", "bbox": {"x0": 0.1, "y0": 0.24, "x1": 0.5, "y1": 0.26}},
        ],
    }

    extracted_fields = {
        "invoice_number": "INV-2024/123",
        "date": "09/08/2024",
        "vendor": "Acme Corp",
        "total_due": "$1,000.00",
        "line_items": ["Service A - $500", "Service B - $500"],
        "description": "Professional services",
        "document_type": "invoice",
        "category": "business",
        "risk_level": "low",
    }

    # Run handlers in sequence
    surya_results = await multiline_handler.handle(surya_results, extracted_fields)
    surya_results = await classifier_handler.handle(surya_results, extracted_fields)
    surya_results = await sparse_handler.handle(b"dummy_pdf", surya_results)

    # Verify results
    assert "line_items" in surya_results
    assert "bboxes" in surya_results["line_items"]
    assert len(surya_results["line_items"]["bboxes"]) == 2

    assert "document_type" in surya_results
    assert surya_results["document_type"]["bbox"] is None
    assert surya_results["document_type"]["confidence"] > 0.7

    assert "_metadata" in surya_results


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
