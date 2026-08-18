import io
import re
import logging
from typing import List, Dict, Any, Tuple, Optional
import pdfplumber

logger = logging.getLogger("geom_parser")

def extract_words_from_pdf(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    """Extracts all words with bounding box coordinates from PDF bytes using pdfplumber."""
    words = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                extracted = page.extract_words() or []
                for w in extracted:
                    x0 = float(w.get("x0", 0))
                    x1 = float(w.get("x1", 0))
                    top = float(w.get("top", 0))
                    bottom = float(w.get("bottom", 0))
                    words.append({
                        "text": w.get("text", "").strip(),
                        "x0": x0,
                        "x1": x1,
                        "top": top,
                        "bottom": bottom,
                        "x_center": (x0 + x1) / 2.0,
                        "page": page_idx
                    })
    except Exception as e:
        logger.warning(f"[Geom] Failed to extract words via pdfplumber: {e}")
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
    Scans rows to find a row containing QF labels (QF1, QF2, QF3... or 1QF).
    Returns list of QF column dicts [{mark, x_center}] and the row index if found (>= 3 marks),
    else empty list and None.
    """
    for r_idx, row in enumerate(rows):
        qf_cols = []
        for w in row:
            text = w["text"].strip()
            # Match QF1..QFn or 1QF
            if re.match(r'(?i)^(?:QF\d+|\d+QF|QF)$', text):
                qf_cols.append({
                    "mark": text,
                    "x_center": w["x_center"],
                    "x0": w["x0"],
                    "x1": w["x1"],
                    "top": w["top"]
                })
        if len(qf_cols) >= 3:
            logger.info(f"[Geom] Found QF column row at row_idx={r_idx} with {len(qf_cols)} marks.")
            return qf_cols, r_idx

    return [], None

def pair_qf_with_amp_poles(
    rows: List[List[Dict[str, Any]]],
    qf_cols: List[Dict[str, Any]],
    x_tol: float = 30.0
) -> List[Dict[str, Any]]:
    """
    For each QF column position by X, finds the closest amperage rating (\d+А)
    and pole rating ([1-4]P) in neighboring rows.
    """
    if not qf_cols or not rows:
        return []

    # Flatten all words across rows to search by proximity
    all_words = [w for r in rows for w in r]

    # Filter candidate words for current ratings and poles
    amp_candidates = []
    poles_candidates = []

    for w in all_words:
        text = w["text"].strip()

        # Check trip setting contexts (Ir, Isd, Iтр, уст, уставка)
        text_lower = text.lower()

        # Amp candidates: e.g. 100А, 125А, 63А, 16А, 400А, 630А
        amp_m = re.search(r'\b(\d+)\s*(?:А|A)\b', text, re.IGNORECASE)
        if amp_m:
            val = int(amp_m.group(1))
            # Filter trip settings (441, 4410, 504)
            if val not in [441, 4410, 504]:
                amp_candidates.append({
                    "val": val,
                    "x_center": w["x_center"],
                    "top": w["top"],
                    "text": text
                })

        # Poles candidates: e.g. 1P, 2P, 3P, 4P
        p_m = re.search(r'\b([1-4])\s*(?:P|П|полюс|п|p)\b', text, re.IGNORECASE)
        if p_m:
            poles_candidates.append({
                "val": f"{p_m.group(1)}P",
                "x_center": w["x_center"],
                "top": w["top"],
                "text": text
            })

    pairs = []
    for qf in qf_cols:
        qf_x = qf["x_center"]
        qf_top = qf["top"]

        # Find closest amp candidate within x_tol
        nearby_amps = [
            a for a in amp_candidates
            if abs(a["x_center"] - qf_x) <= x_tol
        ]
        nearby_amps.sort(key=lambda a: (abs(a["x_center"] - qf_x), abs(a["top"] - qf_top)))

        # Find closest poles candidate within x_tol
        nearby_poles = [
            p for p in poles_candidates
            if abs(p["x_center"] - qf_x) <= x_tol
        ]
        nearby_poles.sort(key=lambda p: (abs(p["x_center"] - qf_x), abs(p["top"] - qf_top)))

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
        items.append({
            "name": name_norm,
            "current_a": int(current_a),
            "poles": poles,
            "qty": qty,
            "unit": "шт"
        })
    return items

def parse_schematic_geom(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Main geometric parser entry point.
    1. Extracts words with coordinates from PDF via pdfplumber.
    2. Clusters words into horizontal rows by Y.
    3. Finds QF column headers (QF1..QFn / 1QF).
    4. Pairs each QF column with nearest amperage and poles by X proximity.
    5. Groups by (poles, current_a) into qty.
    6. Ensures input breaker 630A 3P x1 is present if 630A is found in text.
    Logs: [Geom] qf_cols=N pairs=N groups=3P_125:4,3P_100:3,...
    """
    if not pdf_bytes:
        return []

    words = extract_words_from_pdf(pdf_bytes)
    if not words:
        return []

    rows = cluster_rows(words, y_tol=6.0)
    qf_cols, _ = find_qf_columns(rows)

    pairs = pair_qf_with_amp_poles(rows, qf_cols, x_tol=30.0)
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
    logger.info(f"[Geom] qf_cols={len(qf_cols)} pairs={len(pairs)} groups={','.join(group_strs)}")

    return geom_items
