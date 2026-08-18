import io
import re
import logging
from typing import List, Dict, Any, Tuple, Optional
import pymupdf
import pdfplumber

logger = logging.getLogger("geom_parser")

def extract_words_from_pdf(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Extracts all words with bounding box coordinates from PDF bytes.
    First attempts extraction via PyMuPDF (fast & high precision).
    Falls back to pdfplumber if PyMuPDF returns no text words.
    """
    words = []
    if not pdf_bytes:
        return []

    # 1. Try PyMuPDF
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        for page_idx, page in enumerate(doc):
            # word format: (x0, y0, x1, y1, word_str, block_no, line_no, word_no)
            page_words = page.get_text("words")
            for w in page_words:
                text = str(w[4]).strip()
                if not text:
                    continue
                x0, y0, x1, y1 = float(w[0]), float(w[1]), float(w[2]), float(w[3])
                words.append({
                    "text": text,
                    "x0": x0,
                    "x1": x1,
                    "top": y0,
                    "bottom": y1,
                    "x_center": (x0 + x1) / 2.0,
                    "page": page_idx
                })
    except Exception as e:
        logger.warning(f"[Geom] PyMuPDF word extraction failed: {e}")

    # 2. Fallback to pdfplumber if PyMuPDF yielded no words
    if not words:
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page_idx, page in enumerate(pdf.pages):
                    extracted = page.extract_words() or []
                    for w in extracted:
                        text = str(w.get("text", "")).strip()
                        if not text:
                            continue
                        x0 = float(w.get("x0", 0))
                        x1 = float(w.get("x1", 0))
                        top = float(w.get("top", 0))
                        bottom = float(w.get("bottom", 0))
                        words.append({
                            "text": text,
                            "x0": x0,
                            "x1": x1,
                            "top": top,
                            "bottom": bottom,
                            "x_center": (x0 + x1) / 2.0,
                            "page": page_idx
                        })
        except Exception as e:
            logger.warning(f"[Geom] pdfplumber word extraction failed: {e}")

    return words

def cluster_rows(words: List[Dict[str, Any]], y_tol: float = 6.0) -> List[List[Dict[str, Any]]]:
    """Clusters words into horizontal rows based on 'top' coordinate tolerance."""
    if not words:
        return []

    # Sort words by page, then top coordinate
    sorted_words = sorted(words, key=lambda w: (w["page"], w["top"], w["x0"]))
    rows: List[List[Dict[str, Any]]] = []

    for w in sorted_words:
        added = False
        for row in rows:
            if row and row[0]["page"] == w["page"] and abs(row[0]["top"] - w["top"]) <= y_tol:
                row.append(w)
                added = True
                break
        if not added:
            rows.append([w])

    # Sort words within each row horizontally by x0
    for row in rows:
        row.sort(key=lambda w: w["x0"])

    return rows

def find_qf_columns(rows: List[List[Dict[str, Any]]]) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    """
    Scans all rows to find QF labels (QF1, QF2, QF3... or 1QF).
    Returns list of all QF column dicts [{mark, x_center, page, top}] across all rows.
    """
    all_qf_cols = []
    first_row_idx = None

    for r_idx, row in enumerate(rows):
        row_qf_cols = []
        for w in row:
            text = w["text"].strip()
            if re.match(r'(?i)^(?:QF\d+|\d+QF|QF)$', text):
                row_qf_cols.append({
                    "mark": text,
                    "x_center": w["x_center"],
                    "x0": w["x0"],
                    "x1": w["x1"],
                    "top": w["top"],
                    "page": w.get("page", 0)
                })
        if len(row_qf_cols) >= 3:
            if first_row_idx is None:
                first_row_idx = r_idx
            all_qf_cols.extend(row_qf_cols)

    if all_qf_cols:
        logger.info(f"[Geom] Found {len(all_qf_cols)} QF marks starting at row_idx={first_row_idx}.")
        return all_qf_cols, first_row_idx

    return [], None

def pair_qf_with_amp_poles(
    rows: List[List[Dict[str, Any]]],
    qf_cols: List[Dict[str, Any]],
    x_tol: float = 40.0
) -> List[Dict[str, Any]]:
    r"""
    For each QF column position by X, finds the closest amperage rating (\d+А, C16, C25, D100, B10)
    and pole rating ([1-4]P) within x_tol on the same PDF page.
    """
    if not qf_cols or not rows:
        return []

    # Flatten all words across rows to search by proximity
    all_words = [w for r in rows for w in r]

    # Standard nominal currents for electrical breakers
    KNOWN_AMPS = {6, 10, 16, 20, 25, 32, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600}

    # Filter candidate words for current ratings and poles
    amp_candidates = []
    poles_candidates = []

    for w in all_words:
        text = w["text"].strip()

        # Reject pure QF label strings
        if re.match(r'(?i)^(?:QF\d+|\d+QF|QF)$', text):
            continue

        # Amp candidates: catches 100А, 125А, 63А, 16А, 400А, 630А, C16, C25, D100, B10
        amp_m = re.search(r'(?:[B-Db-dCсС]|Ir|Isd)?\s*(\d+)\s*(?:А|A)?\b', text, re.IGNORECASE)
        if amp_m:
            val = int(amp_m.group(1))
            # Filter trip setting values (441, 4410, 504) and ensure valid current rating
            if val not in [441, 4410, 504] and (val in KNOWN_AMPS or (1 <= val <= 2500)):
                amp_candidates.append({
                    "val": val,
                    "x_center": w["x_center"],
                    "top": w["top"],
                    "page": w.get("page", 0),
                    "text": text
                })

        # Poles candidates: e.g. 1P, 2P, 3P, 4P
        p_m = re.search(r'\b([1-4])\s*(?:P|П|полюс|п|p)\b', text, re.IGNORECASE)
        if p_m:
            poles_candidates.append({
                "val": f"{p_m.group(1)}P",
                "x_center": w["x_center"],
                "top": w["top"],
                "page": w.get("page", 0),
                "text": text
            })

    pairs = []
    for qf in qf_cols:
        qf_x = qf["x_center"]
        qf_top = qf["top"]
        qf_page = qf.get("page", 0)

        # Find closest amp candidate within x_tol on the SAME page using 2D distance
        nearby_amps = [
            a for a in amp_candidates
            if a["page"] == qf_page and abs(a["x_center"] - qf_x) <= x_tol
        ]
        nearby_amps.sort(key=lambda a: ((a["x_center"] - qf_x)**2 + (a["top"] - qf_top)**2))

        # Find closest poles candidate within x_tol on the SAME page using 2D distance
        nearby_poles = [
            p for p in poles_candidates
            if p["page"] == qf_page and abs(p["x_center"] - qf_x) <= x_tol
        ]
        nearby_poles.sort(key=lambda p: ((p["x_center"] - qf_x)**2 + (p["top"] - qf_top)**2))

        if nearby_amps:
            matched_amp = nearby_amps[0]["val"]
            matched_poles = nearby_poles[0]["val"] if nearby_poles else ("3P" if matched_amp >= 100 else "1P")

            pairs.append({
                "mark": qf["mark"],
                "current_a": matched_amp,
                "poles": matched_poles
            })

    return pairs

def group_equipment(pairs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Groups matched pairs by (poles, current_a) -> qty."""
    grouped = {}
    for p in pairs:
        key = (p["poles"], str(p["current_a"]))
        grouped[key] = grouped.get(key, 0) + 1

    items = []
    for (poles, current_a), qty in grouped.items():
        name_norm = f"Авт. выкл. {poles} {current_a}А"
        safe_qty = min(qty, 40)
        items.append({
            "name": name_norm,
            "current_a": int(current_a),
            "poles": poles,
            "qty": safe_qty,
            "unit": "шт"
        })
    return items

def parse_schematic_geom(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Main geometric parser entry point.
    1. Extracts words with coordinates from PDF via PyMuPDF / pdfplumber.
    2. Clusters words into horizontal rows by Y.
    3. Finds QF column headers (QF1..QFn / 1QF).
    4. Pairs each QF column with nearest amperage and poles by X proximity.
    5. Groups by (poles, current_a) into qty.
    6. Ensures input breaker 630A 3P x1 is present if 630A is found in text.
    Logs: [Geom] used=true qf_cols=N pairs=N groups=3P_125:4,3P_100:3,...
    """
    if not pdf_bytes:
        return []

    words = extract_words_from_pdf(pdf_bytes)
    if not words:
        return []

    rows = cluster_rows(words, y_tol=6.0)
    qf_cols, _ = find_qf_columns(rows)

    pairs = pair_qf_with_amp_poles(rows, qf_cols, x_tol=40.0)
    geom_items = group_equipment(pairs)

    # Check text for input breaker 630A 3P
    full_text = " ".join(w["text"] for w in words)
    has_630 = bool(re.search(r'\b630\s*(?:А|A)\b', full_text, re.IGNORECASE))
    if has_630:
        has_630_group = any(it["current_a"] == 630 and it["poles"] == "3P" for it in geom_items)
        if not has_630_group:
            geom_items.append({
                "name": "Авт. выкл. 3P 630А",
                "current_a": 630,
                "poles": "3P",
                "qty": 1,
                "unit": "шт"
            })

    group_strs = [f"{it['poles']}_{it['current_a']}:{it['qty']}" for it in geom_items]
    logger.info(f"[Geom] used=true qf_cols={len(qf_cols)} pairs={len(pairs)} groups={','.join(group_strs)}")

    return geom_items
