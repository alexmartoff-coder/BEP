import os
import pytest
import io
import openpyxl
from fastapi.testclient import TestClient
from backend.main import app
from backend.bom_parser import analyze_equipment
from backend.spec_parser import parse_specification
from backend.price_parser import parse_price_list
from backend.kp_generator import generate_preliminary_kp, export_kp_to_excel

client = TestClient(app)

def test_health_check():
    """Test that the health check returns 200 and correct status."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "bep-backend"}

def test_upload_non_pdf():
    """Test that uploading a non-PDF file to PDF endpoint returns a 400 error."""
    files = {"file": ("test.txt", b"some text content", "text/plain")}
    response = client.post("/api/upload-pdf", files=files)
    assert response.status_code == 400
    assert "Invalid file type" in response.json()["detail"]

def test_bom_parser_helpers():
    """Test the parser regexes with sample BOM text rows."""
    sample_text = (
        "Автоматический выключатель ВА47-29 3P 16A - 4 шт.\n"
        "Контактор КМИ-11810 18А - 2 ед.\n"
        "Реле контроля фаз РНПП-311М - 1 шт.\n"
        "Шкаф металлический ШЭМ-1 800х600 - 1 шт.\n"
        "Клемма заземления EK-4 - 10 шт"
    )
    equipment = analyze_equipment(sample_text)

    assert len(equipment) == 5

    assert "Автоматический выключатель" in equipment[0]["name"]
    assert "ВА47-29" in equipment[0]["name"]
    assert equipment[0]["qty"] == 4

    assert "Контактор" in equipment[1]["name"]
    assert equipment[1]["qty"] == 2

    assert "Реле / Кнопка / Лампа" in equipment[2]["name"]
    assert "РНПП-311М" in equipment[2]["name"]
    assert equipment[2]["qty"] == 1

    assert "Корпус / Шкаф" in equipment[3]["name"]
    assert "ШЭМ-1" in equipment[3]["name"]
    assert equipment[3]["qty"] == 1

    assert "Клеммы / Шины" in equipment[4]["name"]
    assert equipment[4]["qty"] == 10

def create_mock_spec_excel() -> bytes:
    """Helper to create a temporary Technical Specification Excel in-memory."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Спецификация"

    # Headers
    ws.append(["Наименование позиции", "Артикул / Код", "Кол-во", "Ед. изм."])

    # Section 1
    ws.append(["ЩАВР 1200А", "", "", ""])  # Board header
    ws.append(["Автоматический выключатель NM8N-1600S", "CHINT-001", "2", "шт"])
    ws.append(["Контактор NC8-1200", "CHINT-002", "4", "шт"])

    # Section 2
    ws.append(["СШ3 ТП1", "", "", ""])  # Board header
    ws.append(["Кабель силовой ВВГнг", "CABLE-09", "150", "м"])
    ws.append(["Реле контроля фаз РНПП-311М", "CHINT-003", "1", "шт"])

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

def create_mock_price_excel() -> bytes:
    """Helper to create a temporary CHINT Price list Excel in-memory."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Прайс-лист"

    # Headers
    ws.append(["Артикул / Код", "Наименование номенклатуры", "Цена с НДС, руб."])

    # Price records
    ws.append(["CHINT-001", "Автоматический выключатель NM8N-1600S", "45000.50"])
    ws.append(["CHINT-002", "Контактор NC8-1200", "12500.00"])
    ws.append(["CHINT-003", "Реле контроля фаз РНПП-311М", "3200.00"])
    ws.append(["CABLE-09", "Кабель силовой ВВГнг", "120.00"])

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

def test_excel_parsing_and_kp_generation():
    """End-to-end unit test of specification & price list parsing and KP calculation."""
    spec_bytes = create_mock_spec_excel()
    price_bytes = create_mock_price_excel()

    # Test spec parser
    boards = parse_specification(spec_bytes)
    assert len(boards) == 2
    assert boards[0]["board_name"] == "ЩАВР 1200А"
    assert len(boards[0]["items"]) == 2
    assert boards[1]["board_name"] == "СШ3 ТП1"
    assert len(boards[1]["items"]) == 2

    # Test price parser
    price_map = parse_price_list(price_bytes)
    assert len(price_map) > 0

    # Test KP generator
    kp_data = generate_preliminary_kp(boards, price_map)
    assert "boards" in kp_data
    assert kp_data["grand_total"] > 0

    # Validate calculations
    # Board 1: 2 * 45000.50 + 4 * 12500.00 = 90001 + 50000 = 140001
    assert kp_data["boards"][0]["subtotal"] == 140001.0
    # Board 2: 150 * 120 + 1 * 3200 = 18000 + 3200 = 21200
    assert kp_data["boards"][1]["subtotal"] == 21200.0
    # Grand total: 140001 + 21200 = 161201.0
    assert kp_data["grand_total"] == 161201.0

def test_api_generate_kp():
    """Test the FastAPI POST /api/generate-kp endpoint with valid and invalid payloads."""
    spec_bytes = create_mock_spec_excel()
    price_bytes = create_mock_price_excel()

    files = {
        "specification": ("spec.xlsx", spec_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        "pricelist": ("price.xlsx", price_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    }

    response = client.post("/api/generate-kp", files=files)
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "success"
    assert data["specification_file"] == "spec.xlsx"
    assert data["pricelist_file"] == "price.xlsx"
    assert "kp" in data
    assert data["kp"]["grand_total"] == 161201.0

def test_api_export_kp():
    """Test the FastAPI POST /api/export-kp endpoint returns a valid spreadsheet attachment."""
    kp_data = {
        "boards": [
            {
                "board_name": "Тестовый шкаф",
                "items": [
                    {"article": "ART-01", "name": "Тест выключатель", "qty": 5, "unit": "шт", "price": 1000.0, "total": 5000.0, "price_found": True}
                ],
                "subtotal": 5000.0
            }
        ],
        "grand_total": 5000.0
    }

    response = client.post("/api/export-kp", json=kp_data)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "attachment" in response.headers["content-disposition"]

    # Parse returned spreadsheet to ensure correctness
    result_bytes = response.content
    wb = openpyxl.load_workbook(io.BytesIO(result_bytes))
    assert "Коммерческое предложение" in wb.sheetnames
