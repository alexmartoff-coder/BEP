from typing import List, Dict, Any
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import io
from backend.price_parser import clean_key

def generate_preliminary_kp(boards: List[Dict[str, Any]], price_map: Dict[str, float]) -> Dict[str, Any]:
    """
    Combines the parsed board positions with pricing from the CHINT price list.
    Calculates prices, line totals, board subtotals, and grand totals.
    """
    grand_total = 0.0
    kp_boards = []

    for board in boards:
        board_name = board["board_name"]
        board_items = []
        board_subtotal = 0.0

        for item in board["items"]:
            article = item["article"]
            name = item["name"]
            qty = item["qty"]
            unit = item["unit"]

            # Find price with fallback clean keys
            price = 0.0
            price_found = False

            # 1. Match by clean article
            if article:
                cleaned_art = clean_key(article)
                if cleaned_art in price_map:
                    price = price_map[cleaned_art]
                    price_found = True

            # 2. Match by clean name if article match failed
            if not price_found and name:
                cleaned_name = clean_key(name)
                if cleaned_name in price_map:
                    price = price_map[cleaned_name]
                    price_found = True

            # 3. Last resort fuzzy/substring match in price map keys (simple heuristic)
            if not price_found and article:
                art_up = str(article).strip().upper()
                for key in price_map:
                    if key and (key in art_up or art_up in key):
                        price = price_map[key]
                        price_found = True
                        break

            # If no price found, default to 0.0 or a placeholder
            total_sum = price * qty
            board_subtotal += total_sum

            board_items.append({
                "article": article,
                "name": name,
                "qty": qty,
                "unit": unit,
                "price": round(price, 2),
                "total": round(total_sum, 2),
                "price_found": price_found
            })

        grand_total += board_subtotal

        kp_boards.append({
            "board_name": board_name,
            "items": board_items,
            "subtotal": round(board_subtotal, 2)
        })

    return {
        "boards": kp_boards,
        "grand_total": round(grand_total, 2)
    }

def export_kp_to_excel(kp_data: Dict[str, Any]) -> bytes:
    """
    Generates a highly polished and professional commercial offer (КП) spreadsheet.
    Includes styled headers, subtotals, borders, and total sums.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Коммерческое предложение"

    # Enable grid lines
    ws.views.sheetView[0].showGridLines = True

    # Styles
    font_title = Font(name="Calibri", size=16, bold=True, color="1E3A8A")
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_bold = Font(name="Calibri", size=11, bold=True)
    font_regular = Font(name="Calibri", size=11)

    fill_header = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    fill_subtotal = PatternFill(start_color="EFF6FF", end_color="EFF6FF", fill_type="solid")
    fill_total = PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid")

    border_thin = Side(border_style="thin", color="D1D5DB")
    border_double = Side(border_style="double", color="1E3A8A")

    box_border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_thin)
    total_border = Border(top=border_thin, bottom=border_double)

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    # Title Block
    ws.merge_cells("A1:G1")
    ws["A1"] = "ПРЕДВАРИТЕЛЬНОЕ КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ"
    ws["A1"].font = font_title
    ws["A1"].alignment = align_center
    ws.row_dimensions[1].height = 40

    ws["A2"] = "Оборудование: Электрощитовое CHINT"
    ws["A2"].font = font_regular
    ws["A3"] = "Статус: Предварительный расчет"
    ws["A3"].font = font_regular

    # Table headers
    headers = ["№", "Артикул", "Наименование позиции", "Кол-во", "Ед. изм.", "Цена с НДС, руб.", "Сумма с НДС, руб."]
    start_row = 5

    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=start_row, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center
        cell.border = box_border

    ws.row_dimensions[start_row].height = 25

    current_row = start_row + 1
    global_idx = 1

    for board in kp_data["boards"]:
        # Group/Board header row
        ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=7)
        board_cell = ws.cell(row=current_row, column=1, value=f"Раздел: {board['board_name']}")
        board_cell.font = font_bold
        board_cell.fill = fill_subtotal
        board_cell.alignment = align_left
        board_cell.border = Border(bottom=border_thin)
        ws.row_dimensions[current_row].height = 22
        current_row += 1

        # Items
        for item in board["items"]:
            ws.cell(row=current_row, column=1, value=global_idx).alignment = align_center
            ws.cell(row=current_row, column=2, value=item["article"]).alignment = align_left
            ws.cell(row=current_row, column=3, value=item["name"]).alignment = align_left

            qty_cell = ws.cell(row=current_row, column=4, value=item["qty"])
            qty_cell.alignment = align_right
            qty_cell.number_format = "#,##0"

            ws.cell(row=current_row, column=5, value=item["unit"]).alignment = align_center

            price_cell = ws.cell(row=current_row, column=6, value=item["price"])
            price_cell.alignment = align_right
            price_cell.number_format = "#,##0.00"

            total_cell = ws.cell(row=current_row, column=7, value=item["total"])
            total_cell.alignment = align_right
            total_cell.number_format = "#,##0.00"

            # Apply thin border to row cells
            for c in range(1, 8):
                ws.cell(row=current_row, column=c).border = box_border
                ws.cell(row=current_row, column=c).font = font_regular

            ws.row_dimensions[current_row].height = 20
            current_row += 1
            global_idx += 1

        # Board Subtotal row
        ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=6)
        sub_cell_lbl = ws.cell(row=current_row, column=1, value=f"Итого по разделу '{board['board_name']}':")
        sub_cell_lbl.font = font_bold
        sub_cell_lbl.alignment = align_right
        sub_cell_lbl.fill = fill_subtotal

        sub_cell_val = ws.cell(row=current_row, column=7, value=board["subtotal"])
        sub_cell_val.font = font_bold
        sub_cell_val.alignment = align_right
        sub_cell_val.number_format = "#,##0.00"
        sub_cell_val.fill = fill_subtotal

        # borders for subtotal
        for c in range(1, 8):
            ws.cell(row=current_row, column=c).border = box_border

        ws.row_dimensions[current_row].height = 22
        current_row += 1

    # Grand Total row
    ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=6)
    total_cell_lbl = ws.cell(row=current_row, column=1, value="ВСЕГО К ОПЛАТЕ:")
    total_cell_lbl.font = font_bold
    total_cell_lbl.alignment = align_right
    total_cell_lbl.fill = fill_total

    total_cell_val = ws.cell(row=current_row, column=7, value=kp_data["grand_total"])
    total_cell_val.font = font_bold
    total_cell_val.alignment = align_right
    total_cell_val.number_format = "#,##0.00"
    total_cell_val.fill = fill_total

    for c in range(1, 8):
        ws.cell(row=current_row, column=c).border = total_border

    ws.row_dimensions[current_row].height = 26

    # Auto-adjust columns widths
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            # Skip title row and subtotal cells which are merged
            if cell.row in [1, 2, 3] or (cell.row > start_row and cell.column == 1 and "Раздел:" in str(cell.value)):
                continue
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = max(max_len + 3, 10)

    # Limit maximum description column width for readability
    ws.column_dimensions["C"].width = 45

    # Save Workbook to stream
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
