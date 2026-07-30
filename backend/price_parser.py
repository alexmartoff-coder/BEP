import openpyxl
from typing import Dict, Any, Tuple, Optional
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

    # Identify key columns
    col_article_idx = None
    col_name_idx = None
    col_price_idx = None

    rows = list(sheet.iter_rows(values_only=True))

    # Look for header row in the first 35 rows
    for r_idx, row in enumerate(rows[:35]):
        row_vals = [str(val).strip().lower() if val is not None else "" for val in row]

        # Look for headers
        art_found = any("артикул" in val or "код" in val or "ref" in val or "article" in val for val in row_vals)
        price_found = any("цена" in val or "тариф" in val or "retail" in val or "розня" in val or "стоимость" in val for val in row_vals)

        if art_found or price_found:
            for c_idx, val in enumerate(row_vals):
                if "артикул" in val or "код" in val or "ref" in val or "article" in val:
                    col_article_idx = c_idx
                elif "наименован" in val or "описание" in val or "модель" in val or "номенклатура" in val:
                    col_name_idx = c_idx
                elif "цена" in val or "тариф" in val or "retail" in val or "розня" in val or "стоимость" in val:
                    # Prefer prices with VAT if explicitly mentioned
                    if col_price_idx is None or "ндс" in val:
                        col_price_idx = c_idx
            break

    # Default columns if not detected
    if col_article_idx is None:
        col_article_idx = 0
    if col_price_idx is None:
        col_price_idx = 2
    if col_name_idx is None:
        col_name_idx = 1

    # Iterate and extract price maps
    for row in rows:
        if not any(row):
            continue

        art_val = row[col_article_idx] if col_article_idx < len(row) else None
        name_val = row[col_name_idx] if col_name_idx < len(row) else None
        price_val = row[col_price_idx] if col_price_idx < len(row) else None

        if not art_val and not name_val:
            continue

        # Parse price as float
        try:
            if price_val is not None:
                price_str = str(price_val).strip()
                # Clean currency symbols, spaces, etc.
                price_clean = "".join(c for c in price_str if c.isdigit() or c == "." or c == ",")
                price_clean = price_clean.replace(",", ".")
                price = float(price_clean)
            else:
                continue
        except ValueError:
            continue

        # Store clean mapping
        if art_val:
            price_map[clean_key(art_val)] = price
        if name_val:
            price_map[clean_key(name_val)] = price

    return price_map
