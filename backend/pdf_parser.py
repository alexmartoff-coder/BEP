import io
import logging
import os
import tempfile
import re
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
            if not_extracted_text := not extracted_text:
                extracted_text = "Не удалось извлечь текст из PDF. Возможно, файл содержит только отсканированные изображения и OCR библиотека/зависимости не настроены."

    return extracted_text

async def parse_pdf_combined_to_bom(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Parses PDF into structured board groups.
    If Vision successfully returns items, COMPLETELY ignores the text-based parser
    and returns only those items in a single board.
    Otherwise, falls back to text-based parsing with strict junk filtering.
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

    # 2. Check if Vision successfully returned elements
    if vision_items:
        logger.info(f"[Vision] Vision API successfully returned {len(vision_items)} items. COMPLETELY ignoring text-based parser.")
        return [{
            "board_name": "Распознано Vision API",
            "items": vision_items
        }]

    # 3. Fallback to text-based parsing with strict filtering
    logger.info("Vision API returned no items. Falling back to text-based parsing with strict filters.")
    extracted_text = extract_text_from_pdf(pdf_bytes)

    # Filter lines before passing to text parser
    filtered_lines = []
    for line in extracted_text.split("\n"):
        line_stripped = line.strip()
        if not line_stripped:
            continue
        # Discard lines shorter than 5 characters
        if len(line_stripped) < 5:
            continue
        # Discard pure QF+digits lines (e.g. QF1, QF2)
        if re.match(r'(?i)^QF\d+$', line_stripped):
            continue
        # Discard lines with addresses, cadastral info, floor levels
        lower_line = line_stripped.lower()
        if any(kw in lower_line for kw in ["адрес", "по адресу", "кадастр", "уровня пола", "мм от"]):
            continue
        filtered_lines.append(line_stripped)

    filtered_text = "\n".join(filtered_lines)
    text_boards = parse_bom_from_text(filtered_text)

    # Filter board items to keep them pristine
    cleaned_text_boards = []
    for board in text_boards:
        cleaned_items = []
        for item in board.get("items", []):
            item_name = item.get("name", "")
            item_name_lower = item_name.lower()
            if len(item_name) < 5:
                continue
            if re.match(r'(?i)^QF\d+$', item_name):
                continue
            if any(kw in item_name_lower for kw in ["адрес", "по адресу", "кадастр", "уровня пола", "мм от"]):
                continue
            cleaned_items.append(item)

        if cleaned_items:
            cleaned_text_boards.append({
                "board_name": board["board_name"],
                "items": cleaned_items
            })

    return cleaned_text_boards
