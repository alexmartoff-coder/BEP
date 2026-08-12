import pytest
from backend.price_parser import extract_current_a_from_name, parse_price_list_raw, clean_key
from backend.pdf_parser import is_trash_item, text_fallback_scheme_parser

def test_extract_current_a_from_name():
    # 1. Ignore series frame sizes (250 is frame size, 125 is current_a)
    assert extract_current_a_from_name("NM8N-250H 3P 125А") == 125
    assert extract_current_a_from_name("NM8N-250H 3P 125A") == 125

    # 2. Ignore breaking capacities (36кА is breaking capacity, 63 is current_a)
    assert extract_current_a_from_name("NM8N-250H 3P 63А 36кА") == 63
    assert extract_current_a_from_name("NM8N-250H 3P 63А 100kA") == 63

    # 3. Prefer rating value closer to poles indicator
    # "3P" is at index 11. "125A" is closer to "3P" than any other number.
    assert extract_current_a_from_name("NM8N-250H 3P 125A") == 125
    assert extract_current_a_from_name("125A 3P") == 125

def test_is_trash_item():
    assert is_trash_item("мм") is True
    assert is_trash_item("адрес") is True
    assert is_trash_item("QF1") is True  # standard QF designation without nominal rating
    assert is_trash_item("QF1", "C16") is False  # has nominal rating
    assert is_trash_item("Авт. выкл. 3P 125А") is False  # long enough and has nominal rating
    assert is_trash_item("QF", "") is True  # short and no rating

def test_parse_price_list_raw_overlapping_bug():
    # Create mock excel data to test raw price parser
    import openpyxl
    import io

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Артикул", "Наименование", "Тариф с НДС, руб"])
    ws.append(["12345", "Авт. выкл. 3P 125A", "1000.50"])
    ws.append(["67890", "Авт. выкл. 3P 63A", "67890"])  # price equals article -> should trigger warning and price=0.0

    out = io.BytesIO()
    wb.save(out)

    raw_list = parse_price_list_raw(out.getvalue())
    # Exclude header row from counting
    content_rows = [r for r in raw_list if r["Артикул"] != "Артикул"]
    assert len(content_rows) == 2
    assert content_rows[0]["price"] == 1000.50
    assert content_rows[1]["price"] == 0.0  # Safe recovery from price overlap bug!
