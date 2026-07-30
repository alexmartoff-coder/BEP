import os
import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.bom_parser import analyze_equipment

client = TestClient(app)

def test_health_check():
    """Test that the health check returns 200 and correct status."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "bep-backend"}

def test_upload_non_pdf():
    """Test that uploading a non-PDF file returns a 400 error."""
    # Create a mock text file
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

    # Check individual parsed items
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
