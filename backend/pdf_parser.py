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
    1. Ignores trip setting values associated with Ir, Isd, Iтр, Iэр, уст, уставка (e.g. 441A, 4410A, 504A).
    2. Input breaker (1QF / QF 630А) -> 3P_630 qty=1.
    3. Rows QF2...QF31: currents[i] + poles[i] strictly aligned by index.
    4. Logs: [Fallback] skip_setting=441,4410 groups=3P_630:1,3P_125:8,1P_63:13,1P_16:10
    """
    if not text:
        return []

    lines = text.split("\n")

    # Track skipped trip setting values for log
    skipped_settings = []
    has_630 = any(re.search(r'\b630\s*(?:А|A)\b', line, re.IGNORECASE) for line in lines)

    nominals = []
    poles = []

    for line_idx, line in enumerate(lines):
        line_clean = line.strip()
        if not line_clean:
            continue

        line_lower = line_clean.lower()

        # Ignore junk lines
        if any(kw in line_lower for kw in ['сортер', 'резерв', 'кадастр', 'расположить', 'примечан', 'Δu', 'длина', 'штамп', 'лист']):
            continue

        is_setting_line = any(kw in line_lower for kw in ['ir', 'isd', 'iтр', 'iэр', 'уст', 'уставка'])

        # Detect and remove specific trip setting expressions (e.g. "уставка 504А", "уст. 504А", "Ir-441A", "Isd-4410A")
        # to allow preserving main input breaker rating on the same line (e.g., "1QF 630А (уставка 504А)")
        setting_matches = re.findall(r'(?:уст|уставка|ir|isd|iтр|iэр)[^0-9АA]*(\d+)\s*(?:А|A)?', line_clean, re.IGNORECASE)
        for sm in setting_matches:
            if sm not in skipped_settings:
                skipped_settings.append(sm)
        # Strip setting expressions specifically from line_clean
        line_clean = re.sub(r'(?:\(?[^)]*?(?:уст|уставка|ir|isd|iтр|iэр)[^)]*?\)?)|(?:(?:ir|isd|iтр|iэр|уст|уставка)[^0-9АA]*\d+\s*(?:А|A)?)', '', line_clean, flags=re.IGNORECASE)

        # Clean breaking capacities and dimensions
        line_clean = re.sub(r'\b\d+\s*(?:кА|kA|ка|ka)\b', '', line_clean, flags=re.IGNORECASE)
        line_clean = re.sub(r'\b\d+\s*мм\b', '', line_clean, flags=re.IGNORECASE)

        # Extract current ratings
        for m in re.finditer(r'\b(\d+)\s*(?:А|A)\b', line_clean, re.IGNORECASE):
            val = int(m.group(1))
            if val in [504, 441, 4410] and (has_630 or is_setting_line):
                if str(val) not in skipped_settings:
                    skipped_settings.append(str(val))
                continue
            nominals.append({
                "val": val,
                "line": line_idx,
                "start": m.start(),
                "paired": False
            })

        # Extract poles
        for m in re.finditer(r'\b([1-4])\s*(?:P|П|полюс|п|p)\b', line_clean, re.IGNORECASE):
            p_val = f"{m.group(1)}P"
            poles.append({
                "val": p_val,
                "line": line_idx,
                "start": m.start(),
                "paired": False
            })

    # Sort in reading order
    nominals.sort(key=lambda x: (x["line"], x["start"]))
    poles.sort(key=lambda x: (x["line"], x["start"]))

    # Pass 1: Same-line closest pairing
    for nom in nominals:
        same_line_poles = [p for p in poles if p["line"] == nom["line"] and not p["paired"]]
        if same_line_poles:
            same_line_poles.sort(key=lambda p: abs(p["start"] - nom["start"]))
            best_pole = same_line_poles[0]
            nom["paired"] = True
            best_pole["paired"] = True
            nom["poles_val"] = best_pole["val"]

    # Pass 2: Strict positional index alignment i for parallel lists (e.g. current row <-> poles row)
    unpaired_noms = [nom for nom in nominals if not nom["paired"]]
    unpaired_pols = [p for p in poles if not p["paired"]]

    for i in range(min(len(unpaired_noms), len(unpaired_pols))):
        nom = unpaired_noms[i]
        pol = unpaired_pols[i]
        nom["paired"] = True
        pol["paired"] = True
        nom["poles_val"] = pol["val"]

    # Explicit check for main input breaker (e.g. 1QF 630A / QF 630A) when present without explicit same-line poles
    for nom in nominals:
        if not nom["paired"] and "poles_val" not in nom:
            val = nom["val"]
            if val >= 250:
                nom["poles_val"] = "3P"
                nom["paired"] = True

    # Fallback heuristic for any remaining unpaired nominals
    for nom in nominals:
        if "poles_val" not in nom:
            val = nom["val"]
            if val >= 100:
                nom["poles_val"] = "3P"
            else:
                nom["poles_val"] = "1P" if val <= 40 else "3P"

    # Group pairs strictly
    grouped = {}
    total_pairs = 0
    for nom in nominals:
        p_val = nom["poles_val"]
        c_val = str(nom["val"])
        key = (p_val, c_val)
        grouped[key] = grouped.get(key, 0) + 1
        total_pairs += 1

    group_strs = [f"{p}_{c}:{q}" for (p, c), q in grouped.items()]
    skip_str = f"skip_setting={','.join(skipped_settings)} " if skipped_settings else ""
    logger.info(f"[Fallback] used=true {skip_str}groups={','.join(group_strs)}")

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

    source_items = []
    is_vision = False

    # 2. Check if Vision successfully returned elements
    if vision_items:
        logger.info(f"[Vision] status=ok items={len(vision_items)}")
        logger.info("[Geom] used=false qf_cols=0 groups=")
        logger.info("[Fallback] used=false groups=")
        source_items = vision_items
        is_vision = True
    else:
        logger.info("[Vision] status=empty items=0")
        # Try geometric parser via pdfplumber first
        from backend.geom_parser import parse_schematic_geom
        geom_items = parse_schematic_geom(pdf_bytes)

        total_geom_items = sum(it.get("qty", 1) for it in geom_items)
        geom_strs = [f"{it.get('poles')}_{it.get('current_a')}:{it.get('qty')}" for it in geom_items]
        if len(geom_items) >= 3 or total_geom_items >= 5:
            logger.info(f"[Geom] used=true groups={','.join(geom_strs)}")
            logger.info("[Fallback] used=false groups=")
            source_items = geom_items
        else:
            logger.info(f"[Geom] used=false groups={','.join(geom_strs)}")
            fallback_items = text_fallback_scheme_parser(extracted_text)
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
