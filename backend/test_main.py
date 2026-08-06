import os
import pytest
import io
import openpyxl
from fastapi.testclient import TestClient
from backend.main import app
from backend.bom_parser import parse_bom_from_text
from backend.price_parser import parse_price_list
from backend.kp_generator import generate_preliminary_kp, export_kp_to_excel

client = TestClient(app)

def test_health_check():
    """Test that the health check returns 200 and correct status."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "BEP"}

def test_upload_non_pdf():
    """Test that uploading a non-PDF file to PDF endpoint returns a 400 error."""
    files = {"file": ("test.txt", b"some text content", "text/plain")}
    response = client.post("/api/upload-pdf", files=files)
    assert response.status_code == 400
    assert "Invalid file type" in response.json()["detail"]

def test_bom_parser_from_text():
    """Test parsing of equipment and boards from raw flat PDF text."""
    sample_text = (
        "Раздел: ЩАВР 1200А\n"
        "Автоматический выключатель NM8N-1600S 1 шт.\n"
        "Контактор NC8-1200 2 шт.\n"
        "СШ3 ТП1\n"
        "Реле контроля фаз РНПП-311М - 1 шт."
    )
    boards = parse_bom_from_text(sample_text)

    assert len(boards) == 2
    assert boards[0]["board_name"] == "ЩАВР 1200А"
    assert len(boards[0]["items"]) == 2
    assert boards[0]["items"][0]["article"] == "NM8N-1600S"
    assert boards[0]["items"][0]["qty"] == 1

    assert boards[1]["board_name"] == "СШ3 ТП1"
    assert len(boards[1]["items"]) == 1
    assert boards[1]["items"][0]["article"] == "РНПП-311М"
    assert boards[1]["items"][0]["qty"] == 1

def create_mock_price_excel() -> bytes:
    """Helper to create a temporary CHINT Price list Excel in-memory."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Прайс-лист"

    # Headers
    ws.append(["Артикул / Код", "Наименование номенклатуры", "Цена с НДС, руб."])

    # Price records
    ws.append(["NM8N-1600S", "Автоматический выключатель NM8N-1600S", "45000.50"])
    ws.append(["NC8-1200", "Контактор NC8-1200", "12500.00"])
    ws.append(["РНПП-311М", "Реле контроля фаз РНПП-311М", "3200.00"])

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

def test_api_generate_kp_direct():
    """Test the end-to-end PDF + Price List -> KP generation API workflow."""
    # Create a mock PDF content (represented as simple text which pdfplumber/tesseract mock-extracts)
    mock_pdf_content = b"%PDF-1.4 mock text extraction\n"
    # To make our mock PDF extraction return a real spec with boards, we have backend mocks.
    # In this test, we test the API with real uploads.
    price_bytes = create_mock_price_excel()

    # We will upload a PDF file and an Excel price list
    files = [
        ("specification", ("spec.pdf", mock_pdf_content, "application/pdf")),
        ("pricelists", ("price.xlsx", price_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
    ]

    response = client.post("/api/generate-kp", files=files)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["specification_file"] == "spec.pdf"
    assert "kp" in data

def test_api_export_kp():
    """Test exporting commercial proposal payload directly to styled spreadsheet."""
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

def test_build_and_save_index():
    """Test building structured index from price list Excel sheet."""
    from backend.price_parser import build_and_save_index
    price_bytes = create_mock_price_excel()

    index_map = build_and_save_index(price_bytes)
    # The mock price sheet contains NM8N-1600S which matches poles and current
    # NM8N-1600S -> "Автоматический выключатель NM8N-1600S", but let's see if it has poles/current.
    # Our mock excel writes: ["NM8N-1600S", "Автоматический выключатель NM8N-1600S", "45000.50"]
    # Let's create an excel byte stream specifically with poles/current to test extraction.
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Артикул", "Наименование", "Цена"])
    ws.append(["CHINT-125", "Выключатель 3P 125A CHINT", "5000.00"])

    out = io.BytesIO()
    wb.save(out)
    custom_bytes = out.getvalue()

    custom_index = build_and_save_index(custom_bytes)
    assert "3P_125" in custom_index
    assert custom_index["3P_125"][0]["article"] == "CHINT-125"
    assert custom_index["3P_125"][0]["price"] == 5000.0
