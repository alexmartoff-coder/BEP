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

def test_detect_columns_robustness():
    from backend.price_parser import detect_columns

    # Custom Excel table #1: Russian alternate headers
    rows_1 = [
        ("Код товара", "Название позиции", "Цена"),
        ("12345", "Автоматический выключатель CHINT NB2 3P 16A", "150.00"),
        ("67890", "Автоматический выключатель CHINT NB2 3P 32A", "250.00")
    ]
    art_idx, name_idx, price_idx = detect_columns(rows_1)
    assert art_idx == 0
    assert name_idx == 1
    assert price_idx == 2

    # Custom Excel table #2: Scrambled columns & English headers
    rows_2 = [
        ("Price", "Code", "Item Description"),
        ("150.00", "12345", "Автоматический выключатель CHINT NB2 3P 16A"),
        ("250.00", "67890", "Автоматический выключатель CHINT NB2 3P 32A")
    ]
    art_idx, name_idx, price_idx = detect_columns(rows_2)
    assert art_idx == 1
    assert name_idx == 2
    assert price_idx == 0

    # Custom Excel table #3: Missing headers completely (uses numeric fallback & content analysis)
    # The first row is raw values, not headers.
    rows_3 = [
        ("99999", "Автоматический выключатель CHINT DZ158 3P 100A", "1200.50"),
        ("88888", "Автоматический выключатель CHINT DZ158 3P 125A", "1500.00")
    ]
    art_idx, name_idx, price_idx = detect_columns(rows_3)
    assert art_idx == 0
    assert name_idx == 1
    assert price_idx == 2


def test_text_fallback_scheme_parser_parallel_sequences():
    # Simulate parallel sequence lists from diagrams
    input_text = """
    125А 125А 125А 125А 125А 63А 63А 16А 16А 125А 125А 630А
    3P 3P 3P 3P 3P 1P 1P 1P 1P 3P 3P 3P
    ввод 504А
    """
    items = text_fallback_scheme_parser(input_text)

    # 504A should be ignored because 630A is present in the text!
    assert not any(it["current_a"] == "504" for it in items)

    # 630A 3P should be mapped correctly (qty=1)
    item_630 = next(it for it in items if it["current_a"] == "630")
    assert item_630["poles"] == "3P"
    assert item_630["qty"] == 1

    # Check 125A mapping (qty=7)
    item_125 = next(it for it in items if it["current_a"] == "125")
    assert item_125["poles"] == "3P"
    assert item_125["qty"] == 7

    # Check 63A mapping (qty=2)
    item_63 = next(it for it in items if it["current_a"] == "63")
    assert item_63["poles"] == "1P"
    assert item_63["qty"] == 2

    # Check 16A mapping (qty=2)
    item_16 = next(it for it in items if it["current_a"] == "16")
    assert item_16["poles"] == "1P"
    assert item_16["qty"] == 2
