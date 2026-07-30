import openpyxl
from typing import List, Dict, Any, Tuple
import io

def parse_specification(file_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Parses a technical specification Excel file (.xlsx).
    Identifies equipment positions, articles, quantities, units, and groups them by board/cabinet.

    Returns a list of board groups:
    [
        {
            "board_name": "ЩАВР 1200А",
            "items": [
                {"article": "CHINT-123", "name": "Контактор...", "qty": 1, "unit": "шт"}
            ]
        }
    ]
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheet = wb.active

    boards = []
    current_board = {
        "board_name": "Общие позиции",
        "items": []
    }

    # Try to find header column mappings
    # Default mappings (0-indexed column indexes)
    col_name_idx = None
    col_article_idx = None
    col_qty_idx = None
    col_unit_idx = None

    # Simple list of board indicator words
    board_keywords = ["панель", "щит", "шкаф", "щавр", "сш3", "вру", "грэщ", "що-70", "тп", "я5000", "ру-", "вводно"]

    # We iterate and find where the table headers are
    rows = list(sheet.iter_rows(values_only=False))

    # Step 1: Scan for headers
    header_row_found = False
    for r_idx, row in enumerate(rows[:20]): # look in the first 20 rows for headers
        row_vals = [str(cell.value).strip().lower() if cell.value is not None else "" for cell in row]

        # Check if this row looks like a header
        name_found = any("наименован" in val or "описание" in val or "название" in val for val in row_vals)
        qty_found = any("кол" in val or "количество" in val or "объем" in val for val in row_vals)

        if name_found and qty_found:
            header_row_found = True
            for c_idx, val in enumerate(row_vals):
                if "наименован" in val or "описание" in val or "название" in val:
                    col_name_idx = c_idx
                elif "артикул" in val or "код" in val or "шифр" in val or "завод" in val:
                    col_article_idx = c_idx
                elif "кол" in val or "количество" in val or "объем" in val:
                    col_qty_idx = c_idx
                elif "ед" in val or "изм" in val:
                    col_unit_idx = c_idx
            break

    # If headers are not found automatically, assign default indexes
    if col_name_idx is None:
        col_name_idx = 1
    if col_qty_idx is None:
        col_qty_idx = 3
    if col_article_idx is None:
        col_article_idx = 2
    if col_unit_idx is None:
        col_unit_idx = 4

    # Step 2: Iterate and parse content rows
    for r_idx, row in enumerate(rows):
        row_vals_raw = [cell.value for cell in row]
        row_vals_str = [str(val).strip() if val is not None else "" for val in row_vals_raw]

        # Skip completely empty rows
        if not any(row_vals_str):
            continue

        # Helper to check if a row represents a new board/section
        is_board_header = False
        non_empty_vals = [val for val in row_vals_str if val]

        # Rule A: Single non-empty cell in the row containing board indicator keywords or bold
        if len(non_empty_vals) == 1:
            val = non_empty_vals[0]
            if any(kw in val.lower() for kw in board_keywords) or len(val) < 50:
                is_board_header = True
                board_name = val
        # Rule B: A cell is bold and contains a board keyword, and quantity column is empty
        elif not row_vals_str[col_qty_idx]:
            for cell in row:
                if cell.value and cell.font and cell.font.bold:
                    val = str(cell.value).strip()
                    if any(kw in val.lower() for kw in board_keywords):
                        is_board_header = True
                        board_name = val
                        break

        if is_board_header:
            # If current board has items, save it
            if current_board["items"]:
                boards.append(current_board)
            current_board = {
                "board_name": board_name,
                "items": []
            }
            continue

        # Parse standard items
        name_val = row_vals_str[col_name_idx] if col_name_idx < len(row_vals_str) else ""
        if not name_val:
            continue

        # Skip header row itself
        if "наименован" in name_val.lower() or "кол-во" in name_val.lower() or "количество" in name_val.lower():
            continue

        # Extract quantity
        qty_val_str = row_vals_str[col_qty_idx] if col_qty_idx < len(row_vals_str) else "1"
        try:
            # Clean non-digit characters except decimal point
            qty_clean = "".join(c for c in qty_val_str if c.isdigit() or c == "." or c == ",")
            qty_clean = qty_clean.replace(",", ".")
            qty = float(qty_clean) if qty_clean else 1.0
            if qty.is_integer():
                qty = int(qty)
        except:
            qty = 1

        # Extract article
        article = ""
        if col_article_idx is not None and col_article_idx < len(row_vals_str):
            article = row_vals_str[col_article_idx]

        # Extract unit
        unit = "шт"
        if col_unit_idx is not None and col_unit_idx < len(row_vals_str):
            parsed_unit = row_vals_str[col_unit_idx]
            if parsed_unit:
                unit = parsed_unit

        # Skip if quantity is 0 or it's a completely empty item description
        if not name_val or qty <= 0:
            continue

        # Add item to active board group
        current_board["items"].append({
            "name": name_val,
            "article": article,
            "qty": qty,
            "unit": unit
        })

    # Append final active board
    if current_board["items"]:
        boards.append(current_board)

    # Ensure there's at least one board
    if not boards:
        # Fallback parsing: put everything under a generic board name
        generic_items = []
        for r_idx, row in enumerate(rows):
            row_vals_str = [str(cell.value).strip() if cell.value is not None else "" for cell in row]
            if not any(row_vals_str):
                continue
            name = row_vals_str[0] if len(row_vals_str) > 0 else ""
            if name and "наименование" not in name.lower() and "кол-во" not in name.lower():
                qty = 1
                try:
                    qty_str = row_vals_str[2] if len(row_vals_str) > 2 else "1"
                    qty = int(float(qty_str))
                except:
                    pass
                generic_items.append({
                    "name": name,
                    "article": row_vals_str[1] if len(row_vals_str) > 1 else "",
                    "qty": qty,
                    "unit": "шт"
                })
        if generic_items:
            boards.append({
                "board_name": "Техническая спецификация",
                "items": generic_items
            })

    return boards
