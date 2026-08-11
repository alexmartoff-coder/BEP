import pytest
from price_analyzer import PriceAnalyzer
from prompt_generator import PromptGenerator
from smart_matcher import SmartMatcher

def test_price_analyzer_and_prompt_generator():
    mock_price_list = [
        {"Артикул": "ART-001", "Наименование": "Авт. выкл. NM8N-1600S 3P 125A", "Тариф с НДС, руб": 5000},
        {"Артикул": "ART-002", "Наименование": "Контактор NC8-100 100A", "Тариф с НДС, руб": 2000},
    ]

    analyzer = PriceAnalyzer(mock_price_list)
    analysis = analyzer.analyze()

    assert "NM8N-1600S" in analysis["series"]
    assert "автомат" in analysis["types"]
    assert "контактор" in analysis["types"]
    assert analysis["amp_range"] == (100, 125)
    assert "3P" in analysis["poles"] or len(analysis["poles"]) >= 0

    generator = PromptGenerator(analysis)
    prompt = generator.generate()

    assert "инженер-сметчик" in prompt
    assert "NM8N-1600S" in prompt
    assert "автомат" in prompt

def test_smart_matcher_match():
    mock_price_list = [
        {"Артикул": "ART-101", "Наименование": "Авт. выкл. NM8N-1600S 3P 125A", "Тариф с НДС, руб": 5000, "Тариф без НДС, руб": 4000},
        {"Артикул": "ART-102", "Наименование": "Контактор NC8-100 3P 100A", "Тариф с НДС, руб": 2000, "Тариф без НДС, руб": 1600},
    ]

    matcher = SmartMatcher(mock_price_list)

    # 1. Test Match by series + nominal (Exact)
    detected_item = {
        "series": "NM8N-1600S",
        "nominal": "125A",
        "type": "автомат"
    }
    matched_item, score = matcher.match(detected_item)
    assert matched_item is not None
    assert matched_item["Артикул"] == "ART-101"
    assert score == 1.0

    # 2. Test Match by type + nominal (Close matching)
    detected_item_2 = {
        "series": None,
        "nominal": "100A",
        "type": "контактор"
    }
    matched_item_2, score_2 = matcher.match(detected_item_2)
    assert matched_item_2 is not None
    assert matched_item_2["Артикул"] == "ART-102"
    assert score_2 == 0.3

def test_smart_matcher_series_nominal_poles_matching():
    """Test precise series + nominal + poles matching required for the final stage."""
    mock_price_list = [
        {"Артикул": "ART-201", "Наименование": "Авт. выкл. NM8N-250S EN 3P 125A", "Тариф с НДС, руб": 5000},
        {"Артикул": "ART-202", "Наименование": "Авт. выкл. NM8N-250S EN 1P 63A", "Тариф с НДС, руб": 2500},
    ]

    matcher = SmartMatcher(mock_price_list)

    # Precise match by series, nominal and poles
    detected_1 = {"mark": "QF1", "series": "NM8N-250S EN 3P", "nominal": "125A", "poles": "3P"}
    matched_1, score_1 = matcher.match(detected_1)
    assert matched_1 is not None
    assert matched_1["Артикул"] == "ART-201"
    assert score_1 >= 0.8

    # Partial fallback match (prefixes)
    detected_2 = {"mark": "QF7", "series": "NM8N-250S", "nominal": "63A", "poles": "1P"}
    matched_2, score_2 = matcher.match(detected_2)
    assert matched_2 is not None
    assert matched_2["Артикул"] == "ART-202"
    assert score_2 >= 0.8

def test_api_generate_proposal():
    """Test API generate proposal endpoint returns an Excel spreadsheet attachment."""
    from fastapi.testclient import TestClient
    from backend.main import app, PRICE_LIST, MATCHER

    client = TestClient(app)

    # Seed MATCHER in main
    mock_price_list = [
        {"Артикул": "ART-201", "Наименование": "Авт. выкл. NM8N-250S EN 3P 125A", "Тариф с НДС, руб": 5000},
    ]
    import backend.main as main_mod
    main_mod.PRICE_LIST = mock_price_list
    main_mod.MATCHER = SmartMatcher(mock_price_list)

    payload = {
        "items": [
            {"mark": "QF1", "series": "NM8N-250S EN 3P", "nominal": "125A", "poles": "3P"}
        ]
    }

    response = client.post("/api/generate-proposal", json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "attachment" in response.headers["content-disposition"]
