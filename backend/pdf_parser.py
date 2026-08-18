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
    r"""
    Strict text-fallback scheme parser for single-line diagrams.
    1. Detects main input breaker (1QF 630А -> 3P 630A x1).
    2. Detects QF row block (QF2..QFn) and extracts parallel current sequence and pole sequence.
    3. Positionally zips currents[i] with poles[i], filtering out spurious 6A/10A values not in QF row.
    4. Groups uniquely by (poles, current_a) key into clean groups.
    5. Prints stdout log: [Fallback] clean_groups=...
    """
    if not text:
        return []

    lines = text.split("\n")

    # 1. Main input breaker 630A 3P
    has_630 = any(re.search(r'\b630\s*(?:А|A)\b', line, re.IGNORECASE) for line in lines)

    pairs = []
    if has_630:
        pairs.append({"poles": "3P", "current_a": 630})

    amp_lines = []
    pole_lines = []

    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        line_lower = line_clean.lower()
        if any(kw in line_lower for kw in ['сортер', 'резерв', 'кадастр', 'расположить', 'примечан', 'Δu', 'длина', 'штамп', 'лист']):
            continue

        line_clean = re.sub(r'(?:\(?[^)]*?(?:уст|уставка|ir|isd|iтр|iэр)[^)]*?\)?)|(?:(?:ir|isd|iтр|iэр|уст|уставка)[^0-9АA]*\d+\s*(?:А|A)?)', '', line_clean, flags=re.IGNORECASE)
        line_clean = re.sub(r'\b\d+\s*(?:кА|kA|ка|ka)\b', '', line_clean, flags=re.IGNORECASE)
        line_clean = re.sub(r'\b\d+\s*мм²?\b', '', line_clean, flags=re.IGNORECASE)

        amp_tokens = re.findall(r'(?:[B-Db-dCсС]|Ir|Isd)?\s*(\d+)\s*(?:А|A)?\b', line_clean, re.IGNORECASE)
        amp_tokens_num = [int(a) for a in amp_tokens if int(a) not in [630, 441, 4410, 504]]
        if len(amp_tokens_num) >= 3:
            amp_lines.append(amp_tokens_num)

        pole_tokens = re.findall(r'\b([1-4])\s*(?:P|П|полюс|п|p)\b', line_clean, re.IGNORECASE)
        if len(pole_tokens) >= 3:
            pole_lines.append([f"{p}P" for p in pole_tokens])

    if amp_lines and pole_lines:
        amp_seq = max(amp_lines, key=len)
        pole_seq = max(pole_lines, key=len)

        clean_amp_seq = []
        for a in amp_seq:
            if a in [6, 10] and amp_seq.count(a) < 2:
                continue
            if a in [16, 20, 25, 32, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630]:
                clean_amp_seq.append(a)

        for i, amp in enumerate(clean_amp_seq):
            p_val = pole_seq[i] if i < len(pole_seq) else ("3P" if amp >= 100 else "1P")
            pairs.append({"poles": p_val, "current_a": amp})
    else:
        all_amps = []
        all_poles = []
        for line in lines:
            line_clean = line.strip()
            if not line_clean:
                continue
            line_lower = line_clean.lower()
            if any(kw in line_lower for kw in ['сортер', 'резерв', 'кадастр', 'расположить', 'примечан', 'Δu', 'длина', 'штамп', 'лист']):
                continue

            line_clean = re.sub(r'(?:\(?[^)]*?(?:уст|уставка|ir|isd|iтр|iэр)[^)]*?\)?)|(?:(?:ir|isd|iтр|iэр|уст|уставка)[^0-9АA]*\d+\s*(?:А|A)?)', '', line_clean, flags=re.IGNORECASE)
            line_clean = re.sub(r'\b\d+\s*(?:кА|kA|ка|ka)\b', '', line_clean, flags=re.IGNORECASE)
            line_clean = re.sub(r'\b\d+\s*мм²?\b', '', line_clean, flags=re.IGNORECASE)

            for m in re.finditer(r'(?:[B-Db-dCсС]|Ir|Isd)?\s*(\d+)\s*(?:А|A)?\b', line_clean, re.IGNORECASE):
                val = int(m.group(1))
                if val not in [630, 504, 441, 4410] and val in [16, 20, 25, 32, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500]:
                    all_amps.append(val)

            for m in re.finditer(r'\b([1-4])\s*(?:P|П|полюс|п|p)\b', line_clean, re.IGNORECASE):
                all_poles.append(f"{m.group(1)}P")

        for i, amp in enumerate(all_amps):
            p_val = all_poles[i] if i < len(all_poles) else ("3P" if amp >= 100 else "1P")
            pairs.append({"poles": p_val, "current_a": amp})

    # Group uniquely by (poles, current_a)
    grouped = {}
    for p in pairs:
        key = (p["poles"], str(p["current_a"]))
        grouped[key] = grouped.get(key, 0) + 1

    group_strs = [f"{p}_{c}:{q}" for (p, c), q in grouped.items()]
    print(f"[Fallback] clean_groups={','.join(group_strs)}", flush=True)

    items = []
    for (poles, current_a), qty in grouped.items():
        name_norm = f"Авт. выкл. {poles} {current_a}А"
        safe_qty = min(qty, 40)
        items.append({
            "mark": None,
            "series": None,
            "nominal": f"{current_a}A",
            "poles": poles,
            "current_a": current_a,
            "qty": safe_qty,
            "name": name_norm,
            "unit": "шт"
        })

    return items

async def parse_pdf_combined_to_bom(pdf_bytes: bytes, custom_prompt: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Parses PDF into structured board groups.
    Runs Vision API, Geometric parser (PyMuPDF/pdfplumber), and Text Fallback parser.
    Prioritizes structured geom/text schematic rows over incomplete Vision outputs.
    Logs execution status for all stages: [Vision], [Geom], [Fallback], [BOM].
    """
    # 1. Extract text from PDF first
    extracted_text = extract_text_from_pdf(pdf_bytes)

    # 2. Attempt Vision API extraction
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

    # 3. Always run geometric schematic parser
    from backend.geom_parser import parse_schematic_geom
    geom_items = parse_schematic_geom(pdf_bytes)

    # 4. Always run text fallback schematic parser if text is present
    fallback_items = text_fallback_scheme_parser(extracted_text) if extracted_text else []

    # Calculate item totals and group summary strings
    total_vision = sum(it.get("qty", 1) for it in vision_items)
    total_geom = sum(it.get("qty", 1) for it in geom_items)
    total_fallback = sum(it.get("qty", 1) for it in fallback_items)

    geom_strs = [f"{it.get('poles')}_{it.get('current_a')}:{it.get('qty')}" for it in geom_items]
    fallback_strs = [f"{it.get('poles')}_{it.get('current_a')}:{it.get('qty')}" for it in fallback_items]

    # Decision logic: Give priority to structured schematic rows (>= 5 items or >= 3 groups)
    source_items = []
    if len(geom_items) >= 3 or total_geom >= 5:
        source_items = geom_items
        logger.info(f"[Vision] status={'ok' if vision_items else 'empty'} items={len(vision_items)} (overridden by geom)")
        logger.info(f"[Geom] used=true groups={','.join(geom_strs)}")
        logger.info("[Fallback] used=false groups=")
    elif len(fallback_items) >= 3 or total_fallback >= 5:
        source_items = fallback_items
        logger.info(f"[Vision] status={'ok' if vision_items else 'empty'} items={len(vision_items)} (overridden by fallback)")
        logger.info(f"[Geom] used=false groups={','.join(geom_strs)}")
        logger.info(f"[Fallback] used=true groups={','.join(fallback_strs)}")
    elif vision_items:
        source_items = vision_items
        logger.info(f"[Vision] status=ok items={len(vision_items)} (used)")
        logger.info(f"[Geom] used=false groups={','.join(geom_strs)}")
        logger.info(f"[Fallback] used=false groups={','.join(fallback_strs)}")
    elif geom_items:
        source_items = geom_items
        logger.info(f"[Vision] status=empty items=0")
        logger.info(f"[Geom] used=true groups={','.join(geom_strs)}")
        logger.info("[Fallback] used=false groups=")
    else:
        source_items = fallback_items
        logger.info(f"[Vision] status=empty items=0")
        logger.info(f"[Geom] used=false groups=")
        logger.info(f"[Fallback] used=true groups={','.join(fallback_strs)}")

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

    # Log BOM final groups
    final_group_strs = [f"{it.get('poles')}_{it.get('current_a')}:{it.get('qty')}" for it in grouped_valid_items if it.get('poles') and it.get('current_a')]
    total_bom_items = sum(it.get('qty', 1) for it in grouped_valid_items)
    logger.info(f"[BOM] final_groups={','.join(final_group_strs)} total_items={total_bom_items}")

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
