import io
import logging
import os
import tempfile
from typing import List, Dict, Any
from PIL import Image
import pdfplumber
from pdf2image import convert_from_bytes
import pytesseract

from backend.vision_parser import parse_equipment_from_pdf
from backend.bom_parser import parse_bom_from_text

logger = logging.getLogger("pdf_parser")

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    Extracts text from PDF bytes. Uses pdfplumber first.
    If the extracted text is empty or too short, falls back to OCR via pytesseract.
    """
    extracted_text = ""

    # 1. Try extracting text with pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_text = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)
            extracted_text = "\n".join(pages_text).strip()
    except Exception as e:
        logger.warning(f"pdfplumber extraction failed: {e}")

    # 2. Fallback to OCR using pdf2image and pytesseract if text is empty/too short
    if len(extracted_text) < 20:
        logger.info("PDF text content is too short or empty. Falling back to OCR...")
        try:
            # Convert PDF bytes to PIL images
            images = convert_from_bytes(pdf_bytes)
            ocr_pages = []
            for img in images:
                # Perform OCR with Russian and English support
                text = pytesseract.image_to_string(img, lang="rus+eng")
                if text:
                    ocr_pages.append(text)

            ocr_text = "\n".join(ocr_pages).strip()
            if ocr_text:
                extracted_text = ocr_text
        except Exception as e:
            logger.error(f"OCR fallback failed: {e}")
            # If OCR completely fails or tools aren't installed/configured,
            # we keep whatever pdfplumber extracted or return a descriptive fallback string.
            if not extracted_text:
                extracted_text = "Не удалось извлечь текст из PDF. Возможно, файл содержит только отсканированные изображения и OCR библиотека/зависимости не настроены."

    return extracted_text

async def parse_pdf_combined_to_bom(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Parses PDF into structured board groups.
    Combines Gemini Vision API parsing with existing text-based BOM parsing.
    Sums quantities for matched articles/names and appends unique items.
    """
    # 1. First attempt to extract equipment with Vision API
    vision_items = []
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        vision_items = await parse_equipment_from_pdf(tmp_path)
    except Exception as e:
        logger.error(f"Error in Vision parser wrapper: {e}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as e:
                logger.warning(f"Failed to delete temp file {tmp_path}: {e}")

    # 2. Extract text and parse BOM from text
    extracted_text = extract_text_from_pdf(pdf_bytes)
    text_boards = parse_bom_from_text(extracted_text)

    # 3. Fallback check: if Vision returned nothing or very little data, return text_boards
    if not vision_items or len(vision_items) < 2:
        logger.info("Vision API returned empty or insufficient data (<2 items). Falling back to text-based parsing.")
        return text_boards

    if not text_boards:
        logger.info("Text-based parsing returned no boards. Using Vision API results directly.")
        return [{
            "board_name": "Распознано Vision API",
            "items": vision_items
        }]

    # 4. Merging logic: sum quantities for matching items, append unique ones
    # We will modify text_boards in place to keep the original grouping structure
    matched_vision_indices = set()

    # Normalize helper
    def normalize_key(s: str) -> str:
        if not s:
            return ""
        return "".join(c.lower() for c in s if c.isalnum())

    for board in text_boards:
        for text_item in board.get("items", []):
            text_art = normalize_key(text_item.get("article", ""))
            text_name = normalize_key(text_item.get("name", ""))

            for v_idx, v_item in enumerate(vision_items):
                if v_idx in matched_vision_indices:
                    continue

                v_art = normalize_key(v_item.get("article", ""))
                v_name = normalize_key(v_item.get("name", ""))

                # Match by article
                if text_art and v_art and text_art == v_art:
                    text_item["qty"] += v_item["qty"]
                    matched_vision_indices.add(v_idx)
                    logger.info(f"Merged quantities by article: {text_item['article']} (+{v_item['qty']})")
                # Fallback match by name
                elif text_name and v_name and text_name == v_name:
                    text_item["qty"] += v_item["qty"]
                    matched_vision_indices.add(v_idx)
                    logger.info(f"Merged quantities by name: {text_item['name']} (+{v_item['qty']})")

    # For unmatched Vision items, append them to the first board
    unmatched_items = [v_item for v_idx, v_item in enumerate(vision_items) if v_idx not in matched_vision_indices]
    if unmatched_items:
        logger.info(f"Adding {len(unmatched_items)} unmatched Vision items to board '{text_boards[0]['board_name']}'")
        text_boards[0]["items"].extend(unmatched_items)

    return text_boards
