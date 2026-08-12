import io
import logging
import os
import tempfile
import re
from typing import List, Dict, Any, Optional
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

def is_trash_item(item_name: str, nominal: str = "") -> bool:
    """
    Strict filter to identify and discard trash/metadata/junk items.
    Returns True if the item is classified as trash, else False.
    """
    if not item_name:
        return True

    name_lower = item_name.lower()

    # 1. Reject if name contains MM, адрес, кадастр, расположить, примечан, сортер, резерв
    trash_keywords = ["мм", "адрес", "кадастр", "расположить", "примечан", "сортер", "резерв"]
    if any(kw in name_lower for kw in trash_keywords):
        return True

    # 2. Reject if name is just QF + digits (without nominal)
    if re.match(r'(?i)^QF\d+$', item_name.strip()) and not nominal:
        return True

    # 3. Reject if length < 8 characters and lacks numeric rating (number + A/А, or standard curve rating like C16, or any nominal digits)
    has_rating = bool(
        re.search(r'\d+\s*(?:A|А)', item_name, re.IGNORECASE) or
        re.search(r'\d+\s*(?:A|А)', nominal, re.IGNORECASE) or
        re.search(r'\b[B-D]\d+\b', item_name, re.IGNORECASE) or
        re.search(r'\b[B-D]\d+\b', nominal, re.IGNORECASE) or
        (nominal and re.search(r'\d+', nominal))
    )
    if len(item_name.strip()) < 8 and not has_rating:
        return True

    return False

def text_fallback_scheme_parser(text: str) -> List[Dict[str, Any]]:
    """
    Custom text-fallback parser for electrical diagrams.
    Finds poles (1P/3P/4P) and amperage ratings (e.g. 16A, 63А, 125A, 630A, 504A)
    occurring in the text, groups identical rating+poles into items,
    and returns a structured list.
    """
    if not text:
        return []

    lines = text.split("\n")
    grouped = {}

    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue

        # Apply strict trash filter right during text fallback extraction
        if is_trash_item(line_clean):
            continue

        # Extract poles: 1P, 2P, 3P, 4P
        poles_match = re.search(r'\b([1-4])\s*(?:P|П|полюс|п|p)\b', line_clean, re.IGNORECASE)
        poles_val = f"{poles_match.group(1)}P" if poles_match else "3P"

        # Extract amperage: standalone NNN A / А (including special incomers like 630A, 504A)
        current_match = re.search(r'\b(\d+)\s*(?:А|A|а|a)\b', line_clean)
        if not current_match:
            continue

        current_val = current_match.group(1)

        key = (poles_val, current_val)
        grouped[key] = grouped.get(key, 0) + 1

    items = []
    for (poles, current_a), qty in grouped.items():
        name_norm = f"Авт. выкл. {poles} {current_a}А"
        items.append({
            "mark": None,
            "series": None,
            "nominal": f"{current_a}A",
            "poles": poles,
            "current_a": current_a,
            "qty": qty,
            "name": name_norm,
            "unit": "шт"
        })

    return items

async def parse_pdf_combined_to_bom(pdf_bytes: bytes, custom_prompt: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Parses PDF into structured board groups.
    If Vision successfully returns items, COMPLETELY ignores the text-based parser
    and returns only those items in a single board.
    Otherwise, falls back to text-based parsing with strict junk filtering.
    """
    # Extract text from PDF first (to have it ready for fallback)
    extracted_text = extract_text_from_pdf(pdf_bytes)

    # 1. First attempt to extract equipment with Vision API
    vision_items = []
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        vision_items = await parse_equipment_from_pdf(tmp_path, custom_prompt=custom_prompt)
    except Exception as e:
        logger.error(f"[Vision] Error in Vision parser wrapper: {e}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as e:
                logger.warning(f"Failed to delete temp file {tmp_path}: {e}")

    # Log Vision response status
    if vision_items:
        logger.info(f"[Vision] Vision N items: {len(vision_items)}")
    else:
        logger.info("[Vision] Vision empty")

    source_items = []
    is_vision = False

    # 2. Check if Vision successfully returned elements
    if vision_items:
        source_items = vision_items
        is_vision = True
    else:
        # Fallback to custom text-fallback scheme parser
        fallback_items = text_fallback_scheme_parser(extracted_text)
        logger.info(f"[Vision] text-fallback N items: {len(fallback_items)}")
        source_items = fallback_items

    # 3. Apply strict trash filter and normalization
    valid_items = []
    filtered_trash_count = 0

    for item in source_items:
        item_name = str(item.get("name") or item.get("mark") or "")
        nominal_str = str(item.get("nominal") or "")

        # Build strict display name for warning checks
        if not item_name and item.get("poles") and item.get("current_a"):
            item_name = f"Авт. выкл. {item.get('poles')} {item.get('current_a')}А"

        if is_trash_item(item_name, nominal_str):
            filtered_trash_count += 1
            continue

        # Extract current digits from nominal (e.g., '16A' -> '16')
        current_val = str(item.get("current_a") or "")
        if not current_val:
            current_digits_match = re.search(r'\d+', nominal_str)
            current_val = current_digits_match.group(0) if current_digits_match else ""

        # Extract poles from type/mark/nominal, or default to 3P/1P
        poles_val = str(item.get("poles") or "3P")
        if not poles_val or poles_val == "None":
            poles_val = "3P"
            if "1P" in nominal_str.upper() or "1P" in item_name.upper():
                poles_val = "1P"
            elif "2P" in nominal_str.upper():
                poles_val = "2P"
            elif "4P" in nominal_str.upper():
                poles_val = "4P"

        name_norm = f"Авт. выкл. {poles_val} {current_val}А" if current_val else item_name

        valid_items.append({
            "article": str(item.get("mark") or ""),
            "name": name_norm,
            "qty": int(item.get("qty") or 1),
            "unit": "шт",
            "poles": poles_val,
            "current_a": current_val,
            "mark": item.get("mark"),
            "series": item.get("series"),
            "nominal": item.get("nominal"),
            "type": item.get("type")
        })

    logger.info(f"[Vision] {filtered_trash_count} items filtered as trash")

    # Group identical items (by poles and current_a)
    grouped_valid_items = []
    grouped_map = {}
    for vit in valid_items:
        p_val = str(vit.get("poles") or "").upper().strip()
        c_val = str(vit.get("current_a") or "")
        q_val = int(vit.get("qty") or 1)

        if p_val and c_val:
            g_key = (p_val, c_val)
        else:
            g_key = ("RAW", str(vit.get("name") or vit.get("mark") or ""))

        if g_key in grouped_map:
            grouped_map[g_key]["qty"] += q_val
        else:
            grouped_map[g_key] = {
                "article": vit.get("article"),
                "name": vit.get("name"),
                "qty": q_val,
                "unit": "шт",
                "poles": p_val,
                "current_a": c_val,
                "mark": vit.get("mark"),
                "series": vit.get("series"),
                "nominal": vit.get("nominal"),
                "type": vit.get("type")
            }
    grouped_valid_items = list(grouped_map.values())

    # 4. If both Vision and text fallback yield 0 valid elements
    if not grouped_valid_items:
        logger.info("[Vision] No valid items found. Returning warning annotation.")
        return [{
            "board_name": "Распознано Vision API",
            "items": [{
                "article": "",
                "name": "Vision не сработал, автоматы не распознаны",
                "qty": 1,
                "unit": "шт",
                "poles": "",
                "current_a": "",
                "price": 0.0,
                "price_found": False
            }]
        }]

    return [{
        "board_name": "Распознано Vision API",
        "items": grouped_valid_items
    }]
