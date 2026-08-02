import os
import pytest
from unittest.mock import MagicMock, patch
import asyncio
from backend.vision_parser import parse_equipment_from_pdf, compute_pdf_md5
from backend.pdf_parser import parse_pdf_combined_to_bom

# Mock response class for the new google-genai SDK
class MockResponse:
    def __init__(self, parsed_data):
        self.parsed = parsed_data

class MockItem:
    def __init__(self, article, name, qty, unit):
        self.article = article
        self.name = name
        self.qty = qty
        self.unit = unit

def test_vision_parser_success():
    """Test successful Vision parser extraction and parsing with google-genai Client."""
    mock_response_data = [
        MockItem(article="CHINT-001", name="Контактор", qty=3, unit="шт"),
        MockItem(article="CHINT-002", name="Реле времени", qty=1, unit="шт")
    ]

    with patch("google.genai.Client") as mock_client_class, \
         patch("backend.vision_parser.convert_from_path") as mock_convert, \
         patch.dict(os.environ, {"GOOGLE_API_KEY": "fake_key"}):

        # Mock convert_from_path to return some dummy objects representing images
        mock_convert.return_value = [MagicMock(), MagicMock()]

        # Mock genai.Client instance
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = MockResponse(mock_response_data)
        mock_client_class.return_value = mock_client

        # Create a dummy PDF file
        pdf_path = "/tmp/mock_test_vision.pdf"
        with open(pdf_path, "wb") as f:
            f.write(b"mock pdf content")

        try:
            # Clear cache for this file to ensure API is called
            from backend.vision_parser import _memory_cache
            file_hash = compute_pdf_md5(pdf_path)
            _memory_cache.pop(file_hash, None)

            result = asyncio.run(parse_equipment_from_pdf(pdf_path))

            assert len(result) == 2
            assert result[0]["article"] == "CHINT-001"
            assert result[0]["qty"] == 3
            assert result[1]["article"] == "CHINT-002"
            assert result[1]["qty"] == 1

            # Test Cache Hit
            mock_convert.reset_mock()
            cached_result = asyncio.run(parse_equipment_from_pdf(pdf_path))
            assert cached_result == result
            mock_convert.assert_not_called()

        finally:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)

def test_vision_parser_no_api_key():
    """Test that vision parser returns empty list if GOOGLE_API_KEY is not set."""
    # Ensure GOOGLE_API_KEY is not present in mocked env
    with patch.dict(os.environ, {}, clear=True):
        result = asyncio.run(parse_equipment_from_pdf("some_path.pdf"))
        assert result == []

def test_combined_pdf_parser_merging():
    """Test combining and merging logic of Vision + text-based parser."""
    mock_vision_items = [
        {"article": "NM8N-1600S", "name": "Автоматический выключатель", "qty": 2, "unit": "шт"},
        {"article": "NEW-ART", "name": "Новый контактор", "qty": 5, "unit": "шт"}
    ]

    # We will patch extract_text_from_pdf to return simple text
    # that parses into: NM8N-1600S (qty 1) and some other items.
    sample_text = (
        "Раздел: ВРУ\n"
        "Автоматический выключатель NM8N-1600S 1 шт.\n"
    )

    with patch("backend.pdf_parser.parse_equipment_from_pdf", return_value=mock_vision_items), \
         patch("backend.pdf_parser.extract_text_from_pdf", return_value=sample_text):

         # Call the combined parser
         boards = asyncio.run(parse_pdf_combined_to_bom(b"pdf contents"))

         # We expect boards to have ВРУ board
         assert len(boards) == 1
         board = boards[0]
         assert board["board_name"] == "ВРУ"

         # The items should contain NM8N-1600S with SUMMED quantity (1 from text + 2 from Vision = 3)
         # And NEW-ART from Vision appended to the board
         assert len(board["items"]) == 2

         item_nm = next(i for i in board["items"] if i["article"] == "NM8N-1600S")
         assert item_nm["qty"] == 3

         item_new = next(i for i in board["items"] if i["article"] == "NEW-ART")
         assert item_new["qty"] == 5
