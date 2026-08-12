import openpyxl
from typing import Dict, Any, List, Tuple, Optional
import io
import re

def clean_key(val: Any) -> str:
    """Cleans up a code/article/name to allow flexible matching (removes hyphens, spaces, slashes, uppercase)."""
    if val is None:
        return ""
    # Convert to uppercase string, strip spaces, remove hyphens and slashes
    s = str(val).strip().upper()
    return re.sub(r'[^A-Z0-9А-Я]', '', s)

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

        art_found = any("артикул" in val or "код" in val or "article" in val for val in row_vals)
        price_found = any("цена" in val or "тариф" in val or "price" in val or "стоимость" in val for val in row_vals)

        if art_found or price_found:
            for c_idx, val in enumerate(row_vals):
                val_clean = val.lower().strip()
                if any(k in val_clean for k in ["артикул", "код", "article"]):
                    col_article_idx = c_idx
                elif any(k in val_clean for k in ["наименование", "описание", "name"]):
                    col_name_idx = c_idx
                elif any(k in val_clean for k in ["тариф с ндс", "цена с ндс", "цена", "стоимость", "price", "тариф"]):
                    if col_price_idx is None or "ндс" in val_clean or "тариф с ндс" in val_clean or "цена с ндс" in val_clean:
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

    for row in rows:
        if not any(row):
            continue

        art_val = row[col_article_idx] if col_article_idx < len(row) else None
        name_val = row[col_name_idx] if col_name_idx < len(row) else None
        price_val = row[col_price_idx] if col_price_idx < len(row) else None

        if not art_val and not name_val:
            continue

        # Parse price strictly as float
        try:
            if price_val is not None:
                price_str = str(price_val).strip()
                # Clean currency symbols, spaces, and non-breaking spaces strictly!
                price_clean = re.sub(r'[\s\xa0\u200b\u202f\t]+', '', price_str)
                price_clean = price_clean.replace(",", ".")
                price_clean = "".join(c for c in price_clean if c.isdigit() or c == ".")
                price = float(price_clean)
            else:
                continue
        except ValueError:
            continue

        # Store clean mapping, ensuring the article code itself is never used as the price
        if art_val and str(art_val).strip() != price_str:
            price_map[clean_key(art_val)] = price
        if name_val:
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
        art_found = any("артикул" in val or "код" in val or "article" in val for val in row_vals)
        price_found = any("цена" in val or "тариф" in val or "price" in val or "стоимость" in val for val in row_vals)

        if art_found or price_found:
            for c_idx, val in enumerate(row_vals):
                val_clean = val.lower().strip()
                if any(k in val_clean for k in ["артикул", "код", "article"]):
                    col_article_idx = c_idx
                elif any(k in val_clean for k in ["наименование", "описание", "name"]):
                    col_name_idx = c_idx
                elif any(k in val_clean for k in ["тариф с ндс", "цена с ндс", "цена", "стоимость", "price", "тариф"]):
                    if col_price_idx is None or "ндс" in val_clean or "тариф с ндс" in val_clean or "цена с ндс" in val_clean:
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

    unified_items = []

    for row in rows:
        if not any(row):
            continue

        art_val = row[col_article_idx] if col_article_idx < len(row) else None
        name_val = row[col_name_idx] if col_name_idx < len(row) else None
        price_val = row[col_price_idx] if col_price_idx < len(row) else None

        if not name_val:
            continue

        try:
            if price_val is not None:
                price_str = str(price_val).strip()
                # Clean currency symbols, spaces, and non-breaking spaces strictly!
                price_clean = re.sub(r'[\s\xa0\u200b\u202f\t]+', '', price_str)
                price_clean = price_clean.replace(",", ".")
                price_clean = "".join(c for c in price_clean if c.isdigit() or c == ".")
                price = float(price_clean)
            else:
                continue
        except ValueError:
            continue

        name_str = str(name_val).strip()
        article_str = str(art_val).strip() if art_val is not None else ""

        # 1. Extract poles
        poles = None
        poles_match = re.search(r'\b([1-4])\s*(?:P|П|полюс|п|p)\b', name_str, re.IGNORECASE)
        if poles_match:
            poles = f"{poles_match.group(1)}P"

        # 2. Extract current_a (int or None)
        current_a = None
        current_match = re.search(r'\b(\d+)\s*(?:А|A|а|a)\b', name_str)
        if current_match:
            try:
                current_a = int(current_match.group(1))
            except ValueError:
                pass

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
