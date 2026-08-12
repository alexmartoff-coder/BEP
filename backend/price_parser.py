import openpyxl
from typing import Dict, Any, List, Tuple, Optional
import io
import re
import logging

logger = logging.getLogger("price_parser")

def clean_key(val: Any) -> str:
    """Cleans up a code/article/name to allow flexible matching (removes hyphens, spaces, slashes, uppercase)."""
    if val is None:
        return ""
    # Convert to uppercase string, strip spaces, remove hyphens and slashes
    s = str(val).strip().upper()
    return re.sub(r'[^A-Z0-9А-Я]', '', s)

def extract_current_a_from_name(name_str: str) -> Optional[int]:
    """
    Intelligently extracts the nominal current rating (current_a) from name_str.
    - Ignores breaking capacities (e.g. 100кА, 36кА).
    - Ignores series frame sizes (e.g. NM8N-250H).
    - Prefers rating value closer to the poles indicator (e.g. 3P 63А -> 63).
    """
    if not name_str:
        return None

    # 1. Clean breaking capacity (кА / kA)
    name_clean = re.sub(r'\b\d+\s*(?:кА|kA|ка|ka)\b', '', name_str, flags=re.IGNORECASE)

    # 2. Clean product series frame sizes
    name_clean = re.sub(r'\bNM8[N,S]-\d+[A-Z]?\b', '', name_clean, flags=re.IGNORECASE)
    name_clean = re.sub(r'\bNVF7-\d+(?:\.\d+)?[A-Z]?\b', '', name_clean, flags=re.IGNORECASE)
    name_clean = re.sub(r'\bNKB1-\d+\b', '', name_clean, flags=re.IGNORECASE)
    name_clean = re.sub(r'\bNB[2,8]-\d+[A-Z]?\b', '', name_clean, flags=re.IGNORECASE)
    name_clean = re.sub(r'\bNC[1,8]-\d+\b', '', name_clean, flags=re.IGNORECASE)
    name_clean = re.sub(r'\bNR[8,E]-\d+\b', '', name_clean, flags=re.IGNORECASE)

    # Find all poles positions (1P/2P/3P/4P)
    poles_indices = []
    for m in re.finditer(r'\b([1-4])\s*(?:P|П|полюс|п|p)\b', name_clean, re.IGNORECASE):
        poles_indices.append(m.start())

    # Find all amperage rating candidates
    amp_candidates = []
    for m in re.finditer(r'\b(\d+)\s*(?:А|A)\b', name_clean, re.IGNORECASE):
        try:
            val = int(m.group(1))
            amp_candidates.append((val, m.start()))
        except ValueError:
            pass

    if not amp_candidates:
        return None

    if len(amp_candidates) == 1 or not poles_indices:
        return amp_candidates[0][0]

    # Choose candidate closest to any poles indicator
    best_val = amp_candidates[0][0]
    min_dist = float('inf')
    for val, start_idx in amp_candidates:
        for p_idx in poles_indices:
            dist = abs(start_idx - p_idx)
            if dist < min_dist:
                min_dist = dist
                best_val = val

    return best_val

def parse_price_list(file_bytes: bytes, price_map: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """
    Parses a Price List Excel file (.xlsx) and returns a dictionary (or updates existing dictionary)
    mapping cleaned articles or cleaned names to prices with VAT.
    """
    if price_map is None:
        price_map = {}

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheet = wb.active

    col_article_idx = None
    col_name_idx = None
    col_price_idx = None

    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return price_map

    # Heuristically detect columns
    for r_idx, row in enumerate(rows[:35]):
        row_vals = [str(val).strip().lower() if val is not None else "" for val in row]

        art_found = any(val == "артикул" for val in row_vals)
        price_found = any(val in ["тариф с ндс, руб", "тариф с ндс", "цена с ндс", "тариф с ндс, руб.", "цена с ндс, руб.", "цена с ндс, руб"] for val in row_vals)

        if art_found or price_found:
            for c_idx, val in enumerate(row_vals):
                val_clean = val.lower().strip()
                if val_clean == "артикул":
                    col_article_idx = c_idx
                elif any(k in val_clean for k in ["наименование", "описание", "name"]):
                    col_name_idx = c_idx
                elif val_clean in ["тариф с ндс, руб", "тариф с ндс", "цена с ндс", "тариф с ндс, руб.", "цена с ндс, руб.", "цена с ндс, руб"]:
                    col_price_idx = c_idx
            break

    # Prevent article and price columns from overlapping!
    if col_article_idx == col_price_idx and col_article_idx is not None:
        col_price_idx = None

    # Default columns if not detected
    if col_article_idx is None:
        col_article_idx = 0
    if col_name_idx is None:
        col_name_idx = 1

    if col_price_idx is None:
        # Search for a column that is neither article nor name column
        for c in range(len(rows[0])):
            if c != col_article_idx and c != col_name_idx:
                col_price_idx = c
                break
        if col_price_idx is None:
            col_price_idx = 2

    logger.info(f"[Price Parser] col_article_idx={col_article_idx}, col_name_idx={col_name_idx}, col_price_idx={col_price_idx}")

    for row in rows:
        if not any(row):
            continue

        art_val = row[col_article_idx] if col_article_idx < len(row) else None
        name_val = row[col_name_idx] if col_name_idx < len(row) else None
        price_val = row[col_price_idx] if col_price_idx < len(row) else None

        if not art_val and not name_val:
            continue

        # Parse price strictly as float
        price = 0.0
        price_str = ""
        try:
            if price_val is not None:
                price_str = str(price_val).strip()
                # Clean currency symbols, spaces, and non-breaking spaces strictly!
                price_clean = re.sub(r'[\s\xa0\u200b\u202f\t]+', '', price_str)
                price_clean = price_clean.replace(",", ".")
                price_clean = "".join(c for c in price_clean if c.isdigit() or c == ".")
                price = float(price_clean)
        except ValueError:
            price = 0.0

        article_str = str(art_val).strip() if art_val is not None else ""

        # Check if price equals article (this is an error)
        if article_str and (price_str == article_str or clean_key(article_str) == clean_key(price_str)):
            logger.warning(f"[Price Bug] Error: Parsed price equals article code ({price_str}). Setting price to 0.0.")
            price = 0.0

        if art_val and price > 0.0:
            price_map[clean_key(art_val)] = price
        if name_val and price > 0.0:
            price_map[clean_key(name_val)] = price

    return price_map

def parse_excel_to_unified(file_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Parses price list Excel and extracts positions into a unified format list.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheet = wb.active

    col_article_idx = None
    col_name_idx = None
    col_price_idx = None

    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []

    for r_idx, row in enumerate(rows[:35]):
        row_vals = [str(val).strip().lower() if val is not None else "" for val in row]

        art_found = any(val == "артикул" for val in row_vals)
        price_found = any(val in ["тариф с ндс, руб", "тариф с ндс", "цена с ндс", "тариф с ндс, руб.", "цена с ндс, руб.", "цена с ндс, руб"] for val in row_vals)

        if art_found or price_found:
            for c_idx, val in enumerate(row_vals):
                val_clean = val.lower().strip()
                if val_clean == "артикул":
                    col_article_idx = c_idx
                elif any(k in val_clean for k in ["наименование", "описание", "name"]):
                    col_name_idx = c_idx
                elif val_clean in ["тариф с ндс, руб", "тариф с ндс", "цена с ндс", "тариф с ндс, руб.", "цена с ндс, руб.", "цена с ндс, руб"]:
                    col_price_idx = c_idx
            break

    # Prevent article and price columns from overlapping!
    if col_article_idx == col_price_idx and col_article_idx is not None:
        col_price_idx = None

    if col_article_idx is None:
        col_article_idx = 0
    if col_name_idx is None:
        col_name_idx = 1

    if col_price_idx is None:
        # Search for a column that is neither article nor name column
        for c in range(len(rows[0])):
            if c != col_article_idx and c != col_name_idx:
                col_price_idx = c
                break
        if col_price_idx is None:
            col_price_idx = 2

    logger.info(f"[Price Parser Unified] col_article_idx={col_article_idx}, col_name_idx={col_name_idx}, col_price_idx={col_price_idx}")

    unified_items = []

    for row in rows:
        if not any(row):
            continue

        art_val = row[col_article_idx] if col_article_idx < len(row) else None
        name_val = row[col_name_idx] if col_name_idx < len(row) else None
        price_val = row[col_price_idx] if col_price_idx < len(row) else None

        if not name_val:
            continue

        price = 0.0
        price_str = ""
        try:
            if price_val is not None:
                price_str = str(price_val).strip()
                # Clean currency symbols, spaces, and non-breaking spaces strictly!
                price_clean = re.sub(r'[\s\xa0\u200b\u202f\t]+', '', price_str)
                price_clean = price_clean.replace(",", ".")
                price_clean = "".join(c for c in price_clean if c.isdigit() or c == ".")
                price = float(price_clean)
        except ValueError:
            price = 0.0

        name_str = str(name_val).strip()
        article_str = str(art_val).strip() if art_val is not None else ""

        # Check if price equals article (this is an error)
        if article_str and (price_str == article_str or clean_key(article_str) == clean_key(price_str)):
            logger.warning(f"[Price Bug] Error: Parsed price equals article code ({price_str}). Setting price to 0.0.")
            price = 0.0

        # 1. Extract poles
        poles = None
        poles_match = re.search(r'\b([1-4])\s*(?:P|П|полюс|п|p)\b', name_str, re.IGNORECASE)
        if poles_match:
            poles = f"{poles_match.group(1)}P"

        # 2. Extract current_a using the highly intelligent amperage extractor
        current_a = extract_current_a_from_name(name_str)

        # 3. Extract series
        series = None
        patterns = [
            r'(NM8[N,S]-\d+[A-Z]?)',
            r'(NVF7-\d+[A-Z]?)',
            r'(NKB1-\d+)',
            r'(NB[2,8]-\d+[A-Z]?)',
            r'(NC[1,8]-\d+)',
            r'(NR[8,E]-\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, name_str)
            if match:
                series = match.group(0)
                break

        unified_items.append({
            "article": article_str,
            "name": name_str,
            "price": price,
            "current_a": current_a,
            "poles": poles,
            "series": series
        })

    return unified_items

def build_and_save_index(file_bytes: bytes) -> Dict[str, Any]:
    """
    Builds structured index {poles}_{current_a} -> list of unified positions.
    """
    unified_items = parse_excel_to_unified(file_bytes)
    index_map = {}
    for item in unified_items:
        poles = item.get("poles")
        current_a = item.get("current_a")
        if poles and current_a:
            key = f"{poles}_{current_a}"
            if key not in index_map:
                index_map[key] = []
            index_map[key].append(item)
    return index_map

def parse_price_list_raw(file_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Parses a Price List Excel file and returns a list of dictionaries with original keys:
    'Артикул', 'Наименование', 'Тариф с НДС, руб', and 'price'.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheet = wb.active

    col_article_idx = None
    col_name_idx = None
    col_price_idx = None

    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []

    for r_idx, row in enumerate(rows[:35]):
        row_vals = [str(val).strip().lower() if val is not None else "" for val in row]

        art_found = any(val == "артикул" for val in row_vals)
        price_found = any(val in ["тариф с ндс, руб", "тариф с ндс", "цена с ндс", "тариф с ндс, руб.", "цена с ндс, руб.", "цена с ндс, руб"] for val in row_vals)

        if art_found or price_found:
            for c_idx, val in enumerate(row_vals):
                val_clean = val.lower().strip()
                if val_clean == "артикул":
                    col_article_idx = c_idx
                elif any(k in val_clean for k in ["наименование", "описание", "name"]):
                    col_name_idx = c_idx
                elif val_clean in ["тариф с ндс, руб", "тариф с ндс", "цена с ндс", "тариф с ндс, руб.", "цена с ндс, руб.", "цена с ндс, руб"]:
                    col_price_idx = c_idx
            break

    if col_article_idx == col_price_idx and col_article_idx is not None:
        col_price_idx = None

    if col_article_idx is None:
        col_article_idx = 0
    if col_name_idx is None:
        col_name_idx = 1

    if col_price_idx is None:
        for c in range(len(rows[0])):
            if c != col_article_idx and c != col_name_idx:
                col_price_idx = c
                break
        if col_price_idx is None:
            col_price_idx = 2

    raw_list = []
    for row in rows:
        if not any(row):
            continue

        art_val = row[col_article_idx] if col_article_idx < len(row) else None
        name_val = row[col_name_idx] if col_name_idx < len(row) else None
        price_val = row[col_price_idx] if col_price_idx < len(row) else None

        if not name_val:
            continue

        price = 0.0
        price_str = ""
        try:
            if price_val is not None:
                price_str = str(price_val).strip()
                price_clean = re.sub(r'[\s\xa0\u200b\u202f\t]+', '', price_str)
                price_clean = price_clean.replace(",", ".")
                price_clean = "".join(c for c in price_clean if c.isdigit() or c == ".")
                price = float(price_clean)
        except ValueError:
            price = 0.0

        article_str = str(art_val).strip() if art_val is not None else ""

        # Check if price equals article (this is an error)
        if article_str and (price_str == article_str or clean_key(article_str) == clean_key(price_str)):
            logger.warning(f"[Price Bug] Error: Parsed price equals article code ({price_str}). Setting price to 0.0.")
            price = 0.0

        raw_list.append({
            "Артикул": article_str,
            "Наименование": str(name_val).strip(),
            "Тариф с НДС, руб": price,
            "price": price
        })

    return raw_list
