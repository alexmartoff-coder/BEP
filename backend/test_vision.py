import os
import pytest
from unittest.mock import MagicMock, patch
import asyncio
from backend.vision_parser import parse_equipment_from_pdf, compute_pdf_md5
from backend.pdf_parser import parse_pdf_combined_to_bom

# Mock response class for OpenAI chat completion
class MockMessage:
    def __init__(self, content):
        self.content = content

class MockChoice:
    def __init__(self, content):
        self.message = MockMessage(content)

class MockCompletionResponse:
    def __init__(self, content):
        self.choices = [MockChoice(content)]

def test_vision_parser_success():
    """Test successful Vision parser extraction and parsing with OpenRouter client and new schema."""
    mock_json_response = """
    [
      {"mark": "QF1", "nominal": "C16", "type": "MCB"},
      {"mark": "QF2", "nominal": "25A", "type": "MCB"}
    ]
    """

    with patch("backend.vision_parser.OpenAI") as mock_openai_class, \
         patch("backend.vision_parser.convert_from_path") as mock_convert, \
         patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake_key"}):

        # Mock convert_from_path to return some dummy objects representing images with valid sizes
        mock_img1 = MagicMock()
        mock_img1.size = (100, 100)
        mock_img2 = MagicMock()
        mock_img2.size = (100, 100)
        mock_convert.return_value = [mock_img1, mock_img2]

        # Mock OpenAI Client instance
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = MockCompletionResponse(mock_json_response)
        mock_openai_class.return_value = mock_client

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
            assert result[0]["mark"] == "QF1"
            assert result[0]["nominal"] == "C16"
            assert result[0]["type"] == "MCB"
            assert result[1]["mark"] == "QF2"
            assert result[1]["nominal"] == "25A"
            assert result[1]["type"] == "MCB"

            # Test Cache Hit
            mock_convert.reset_mock()
            cached_result = asyncio.run(parse_equipment_from_pdf(pdf_path))
            assert cached_result == result
            mock_convert.assert_not_called()

        finally:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)

def test_vision_parser_duplicate_grouping():
    """Test that Vision parser successfully returns devices in the correct format."""
    mock_json_response = """
    [
      {"mark": "QF1", "nominal": "16A", "type": "MCB"},
      {"mark": "QF2", "nominal": "16A", "type": "MCB"}
    ]
    """

    with patch("backend.vision_parser.OpenAI") as mock_openai_class, \
         patch("backend.vision_parser.convert_from_path") as mock_convert, \
         patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake_key"}):

        mock_img = MagicMock()
        mock_img.size = (100, 100)
        mock_convert.return_value = [mock_img]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = MockCompletionResponse(mock_json_response)
        mock_openai_class.return_value = mock_client

        pdf_path = "/tmp/mock_test_vision_grouping.pdf"
        with open(pdf_path, "wb") as f:
            f.write(b"mock pdf content")

        try:
            # Clear cache
            from backend.vision_parser import _memory_cache
            file_hash = compute_pdf_md5(pdf_path)
            _memory_cache.pop(file_hash, None)

            result = asyncio.run(parse_equipment_from_pdf(pdf_path))

            assert len(result) == 2
            assert result[0]["mark"] == "QF1"
            assert result[1]["mark"] == "QF2"

        finally:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)

def test_vision_parser_fallback_404():
    """Test that vision parser successfully falls back to Gemma 4 when primary model returns 404/429."""
    mock_json_response = """
    [
      {"mark": "FALLBACK-001", "nominal": "C16", "type": "MCB"}
    ]
    """

    with patch("backend.vision_parser.OpenAI") as mock_openai_class, \
         patch("backend.vision_parser.convert_from_path") as mock_convert, \
         patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake_key"}):

        mock_img = MagicMock()
        mock_img.size = (100, 100)
        mock_convert.return_value = [mock_img]

        mock_client = MagicMock()

        # Side effect to raise Exception on primary model and return success on fallback model
        def completions_side_effect(*args, **kwargs):
            model = kwargs.get("model")
            if model == "google/gemma-4-26b-a4b-it:free":
                ex = Exception("404 Not Found")
                ex.status_code = 404
                raise ex
            elif model == "google/gemma-4-31b-it:free":
                return MockCompletionResponse(mock_json_response)
            else:
                raise Exception("Unexpected model called")

        mock_client.chat.completions.create.side_effect = completions_side_effect
        mock_openai_class.return_value = mock_client

        pdf_path = "/tmp/mock_test_vision_fallback.pdf"
        with open(pdf_path, "wb") as f:
            f.write(b"mock fallback pdf content")

        try:
            # Clear cache
            from backend.vision_parser import _memory_cache
            file_hash = compute_pdf_md5(pdf_path)
            _memory_cache.pop(file_hash, None)

            result = asyncio.run(parse_equipment_from_pdf(pdf_path))

            assert len(result) == 1
            assert result[0]["mark"] == "FALLBACK-001"
            assert result[0]["nominal"] == "C16"

            # Verify that create was indeed called twice
            assert mock_client.chat.completions.create.call_count == 2

        finally:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)

def test_vision_parser_no_api_key():
    """Test that vision parser returns empty list if OPENROUTER_API_KEY is not set."""
    # Ensure OPENROUTER_API_KEY is not present in mocked env
    with patch.dict(os.environ, {}, clear=True):
        result = asyncio.run(parse_equipment_from_pdf("some_path.pdf"))
        assert result == []

def test_combined_pdf_parser_merging():
    """Test combining and merging logic of Vision + text-based parser."""
    mock_vision_items = [
        {"mark": "QF1", "nominal": "C16", "type": "MCB"},
        {"mark": "QF2", "nominal": "25A", "type": "MCB"}
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

         # We expect boards to contain only Vision items and completely ignore text parser
         assert len(boards) == 1
         board = boards[0]
         assert board["board_name"] == "Распознано Vision API"
         assert len(board["items"]) == 2

         item_nm = next(i for i in board["items"] if i["mark"] == "QF1")
         assert item_nm["nominal"] == "C16"
