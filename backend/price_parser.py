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

def detect_columns(rows: List[Tuple[Any, ...]]) -> Tuple[int, int, int]:
    """
    Intelligently and robustly detects the column indices for:
    - Article (Артикул)
    - Name (Наименование)
    - Price (Цена/Тариф с НДС)
    Returns a tuple of (col_article_idx, col_name_idx, col_price_idx).
    """
    if not rows:
        return 0, 1, 2

    num_cols = len(rows[0])
    if num_cols == 0:
        return 0, 1, 2

    article_scores = [0] * num_cols
    name_scores = [0] * num_cols
    price_scores = [0] * num_cols

    # 1. Header Row Search
    header_r_idx = -1
    for r_idx in range(min(15, len(rows))):
        row = rows[r_idx]
        row_strs = [str(cell).strip().lower() for cell in row if cell is not None]
        has_art = any(any(kw in s for kw in ['артикул', 'код', 'sku', 'article']) for s in row_strs)
        has_price = any(any(kw in s for kw in ['цена', 'тариф', 'стоимость', 'price']) for s in row_strs)
        has_name = any(any(kw in s for kw in ['наимен', 'назван', 'товар', 'описан', 'name', 'item']) for s in row_strs)

        if (has_art and has_price) or (has_name and has_price) or (has_art and has_name):
            header_r_idx = r_idx
            break

    scan_rows = [header_r_idx] if header_r_idx != -1 else list(range(min(5, len(rows))))

    for r_idx in scan_rows:
        if r_idx < 0 or r_idx >= len(rows):
            continue
        row = rows[r_idx]
        for c_idx in range(min(num_cols, len(row))):
            val = row[c_idx]
            if val is None:
                continue
            val_str = str(val).strip().lower()
            if not val_str:
                continue

            # Price matches
            if any(kw in val_str for kw in ['цена с ндс', 'тариф с ндс', 'тариф', 'цена', 'стоимость', 'price', 'cost', 'rate']):
                price_scores[c_idx] += 150
            elif 'ндс' in val_str or 'руб' in val_str:
                price_scores[c_idx] += 80

            # Name matches
            if any(kw in val_str for kw in ['наименование', 'номенклатура', 'название', 'описание', 'name', 'description', 'item', 'позиция']):
                if not any(kw in val_str for kw in ['код', 'артикул', 'арт', 'sku', 'article']):
                    name_scores[c_idx] += 150
            elif 'товар' in val_str:
                if not any(kw in val_str for kw in ['код', 'артикул', 'арт', 'sku', 'article']):
                    name_scores[c_idx] += 80

            # Article matches
            if any(kw in val_str for kw in ['код товара', 'код номенклатуры', 'артикул', 'код', 'арт.', 'арт', 'sku', 'article', 'code']):
                article_scores[c_idx] += 150
            elif 'id' in val_str:
                article_scores[c_idx] += 80

    # 2. Content analysis on remaining rows
    start_content_row = header_r_idx + 1 if header_r_idx != -1 else 0
    for r_idx in range(start_content_row, len(rows)):
        row = rows[r_idx]
        for c_idx in range(min(num_cols, len(row))):
            val = row[c_idx]
            if val is None:
                continue
            val_str = str(val).strip()
            if not val_str:
                continue

            is_num = False
            clean_str = re.sub(r'[\s\xa0\u200b\u202f\tруб\$€₽]+', '', val_str).replace(',', '.')
            if re.match(r'^\d+(\.\d+)?$', clean_str):
                is_num = True
                val_float = float(clean_str)

            if is_num:
                if '.' in clean_str:
                    price_scores[c_idx] += 5
                else:
                    price_scores[c_idx] += 1
                    article_scores[c_idx] += 1
            else:
                if len(val_str) > 15 and ' ' in val_str:
                    name_scores[c_idx] += 5
                elif 3 <= len(val_str) <= 25:
                    article_scores[c_idx] += 3

    # Selection logic
    best_price_idx = 0
    max_price = -1
    for c in range(num_cols):
        if price_scores[c] > max_price:
            max_price = price_scores[c]
            best_price_idx = c

    best_name_idx = 0
    max_name = -1
    for c in range(num_cols):
        if c == best_price_idx:
            continue
        if name_scores[c] > max_name:
            max_name = name_scores[c]
            best_name_idx = c

    best_article_idx = 0
    max_article = -1
    for c in range(num_cols):
        if c == best_price_idx or c == best_name_idx:
            continue
        if article_scores[c] > max_article:
            max_article = article_scores[c]
            best_article_idx = c

    if num_cols == 1:
        return 0, 0, 0
    elif num_cols == 2:
        return 0, best_name_idx, best_price_idx

    logger.info(f"[Detect Columns] Selected: Article={best_article_idx}, Name={best_name_idx}, Price={best_price_idx}")
    return best_article_idx, best_name_idx, best_price_idx


def parse_price_list(file_bytes: bytes, price_map: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """
    Parses a Price List Excel file (.xlsx) and returns a dictionary (or updates existing dictionary)
    mapping cleaned articles or cleaned names to prices with VAT.
    """
    if price_map is None:
        price_map = {}

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheet = wb.active

    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return price_map

    col_article_idx, col_name_idx, col_price_idx = detect_columns(rows)
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

    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []

    col_article_idx, col_name_idx, col_price_idx = detect_columns(rows)
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

    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return []

    col_article_idx, col_name_idx, col_price_idx = detect_columns(rows)
    logger.info(f"[Price Parser Raw] col_article_idx={col_article_idx}, col_name_idx={col_name_idx}, col_price_idx={col_price_idx}")

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
