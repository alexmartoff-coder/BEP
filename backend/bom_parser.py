import re
from typing import List, Dict, Any

# Regular expression to find potential board/cabinet section names
BOARD_PATTERN = re.compile(
    r"\b(?:раздел|панель|щит|шкаф|вру|грэщ|що-70|щавр|сш\d*|тп\d*|ру-\d*|я\d{4}|ящик|оболочка)\s*:\s*([\w\d\-А-Яа-я]+(?:\s+[\w\d\-А-Яа-я]+)*)",
    re.IGNORECASE
)

# Broad categories of switchboard equipment we look for
CATEGORIES = [
    {
        "name": "Автоматический выключатель",
        "keywords": ["выключатель", "автомат", "ва47", "ва57", "ва88", "bm63", "optidin", "nm8", "qf"]
    },
    {
        "name": "Контактор",
        "keywords": ["контактор", "пускатель", "кми", "nc8", "пмл", "пм12", "км"]
    },
    {
        "name": "Реле / Контроль",
        "keywords": ["реле", "рнпп", "рэк", "рп-", "ртл", "рти", "контроль"]
    },
    {
        "name": "Кнопка / Сигнализация",
        "keywords": ["кнопка", "лампа", "арматура", "лс-", "ad22", "sb", "hl"]
    },
    {
        "name": "Клеммы / Шины",
        "keywords": ["клемма", "шина", "шни", "pen", "кросс-модуль", "cross-block"]
    },
    {
        "name": "Трансформатор тока",
        "keywords": ["трансформатор", "тт", "тахограф", "ток"]
    },
    {
        "name": "Корпус / Шкаф",
        "keywords": ["корпус", "шкаф", "ящик", "щмп", "щрн", "щрв", "шэм"]
    }
]

# Quantities pattern: looks for numbers near "шт", "шт.", "x", "х", or at the end of lines
QTY_PATTERN = re.compile(
    r"(?:кол-во|количество|кол\.?|x|х)?\s*(\d+)\s*(?:шт\.?|ед\.?|компл\.?|м\.?|\-|$)",
    re.IGNORECASE
)

# Extract articles (alphanumeric sequences with hyphens/slashes of length >= 5)
ARTICLE_PATTERN = re.compile(
    r"\b([A-Z0-9А-Я]{3,}\-[A-Z0-9А-Я\-/\.]{2,}\b|[A-Z]{2,}\d{2,}\b|[A-Z\d\-]{5,})\b"
)

def parse_bom_from_text(text: str) -> List[Dict[str, Any]]:
    """
    Parses a flat PDF text block into structured board groups.
    Each group has a 'board_name' and a list of parsed 'items'.

    Returns:
    [
        {
            "board_name": "ЩАВР 1200А",
            "items": [
                {"article": "NM8N-1600S", "name": "Автоматический выключатель...", "qty": 2, "unit": "шт"}
            ]
        }
    ]
    """
    if not text:
        return []

    lines = [line.strip() for line in text.split("\n") if line.strip()]

    boards = []
    current_board = {
        "board_name": "Общие позиции",
        "items": []
    }

    for line in lines:
        is_board = False
        board_name = ""

        # Heuristic 1: line contains key board designators and is relatively short
        if len(line) < 45:
            lower_line = line.lower()
            if any(kw in lower_line for kw in ["сш3", "тп1", "тп2", "щавр", "щмп", "вру-", "я5000", "щит ", "шкаф "]):
                is_board = True
                board_name = line
                # strip section/раздел prefix if present
                board_name = re.sub(r'^(?:раздел|щит|шкаф|панель)\s*:\s*', '', board_name, flags=re.IGNORECASE).strip()

        # Heuristic 2: explicit board pattern regex
        match_board = BOARD_PATTERN.search(line)
        if match_board and not is_board:
            if not any(kw in line.lower() for kw in ["автомат", "выключатель", "контактор", "клемма"]):
                is_board = True
                board_name = match_board.group(1).strip()

        if is_board and board_name:
            if current_board["items"]:
                boards.append(current_board)
            current_board = {
                "board_name": board_name,
                "items": []
            }
            continue

        is_item = False
        category_name = "Оборудование"

        for cat in CATEGORIES:
            if any(kw in line.lower() for kw in cat["keywords"]):
                is_item = True
                category_name = cat["name"]
                break

        art_match = ARTICLE_PATTERN.search(line)
        if art_match and not is_item:
            is_item = True

        if is_item:
            article = ""
            if art_match:
                article = art_match.group(1)

            qty = 1
            text_for_qty = line
            if article:
                text_for_qty = text_for_qty.replace(article, "")

            qty_matches = QTY_PATTERN.findall(text_for_qty)
            if qty_matches:
                for q in qty_matches:
                    if q.isdigit() and int(q) > 0 and len(q) < 5:
                        qty = int(q)
                        break

            clean_name = line
            if article:
                clean_name = clean_name.replace(article, "")
            clean_name = re.sub(r'\b\d+\s*(?:шт\.?|ед\.?|компл\.?|м\.?)\b', '', clean_name, flags=re.IGNORECASE)
            clean_name = re.sub(r'^\s*[\d\.\-\:\)\#№\s]+', '', clean_name)
            clean_name = clean_name.strip(" -.,:;")

            if not clean_name:
                clean_name = f"{category_name} {article}".strip()
            if not clean_name:
                clean_name = line

            if len(clean_name) > 120:
                clean_name = clean_name[:120] + "..."

            current_board["items"].append({
                "article": article,
                "name": clean_name,
                "qty": qty,
                "unit": "шт"
            })

    if current_board["items"]:
        boards.append(current_board)

    return boards

def analyze_equipment(text: str) -> List[Dict[str, Any]]:
    """
    Backwards compatibility method.
    Returns flat list of parsed equipment elements.
    """
    boards = parse_bom_from_text(text)
    flat_items = []
    item_id = 1

    for board in boards:
        for item in board["items"]:
            display_name = item["name"]
            if item["article"] and item["article"] not in display_name:
                display_name = f"{display_name} ({item['article']})"

            flat_items.append({
                "id": item_id,
                "name": display_name,
                "qty": item["qty"],
                "quantity": item["qty"],
                "unit": item["unit"]
            })
            item_id += 1

    return flat_items
