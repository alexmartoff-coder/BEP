import re
from typing import List, Dict, Any

# Regex patterns for typical switchboard equipment
PATTERNS = [
    # 1. Automatic switches (Автоматические выключатели)
    # Examples: OptiDin BM63-1C16, ВА47-29, BM63, QF1
    {
        "category": "Автоматический выключатель",
        "regex": re.compile(
            r"(?:автоматический\s+выключатель|выключатель\s+автоматический|автомат|QF\d*)\s+([\w\d\-]+(?:\s+[\w\d\-]+)*)|([\w\d\-]+(?:\s+[\w\d\-]+)*)\s+(?:OptiDin|BM63|ВА47|ВА57|ВА88|ВА-?\d+)",
            re.IGNORECASE
        ),
        "fallback_keywords": ["ВА47", "ВА57", "ВА88", "OptiDin", "BM63", "BM160", "BM250", "BM630"]
    },
    # 2. Contactors (Контакторы)
    # Examples: КМ1, КМ-25, ПМ12-010100, ПМЛ-1100
    {
        "category": "Контактор",
        "regex": re.compile(
            r"(?:контактор|пускатель|КМ\d*)\s+([\w\d\-]+(?:\s+[\w\d\-]+)*)|([\w\d\-]+(?:\s+[\w\d\-]+)*)\s+(?:ПМ12|ПМЛ|КМИ|КТ|КТП)",
            re.IGNORECASE
        ),
        "fallback_keywords": ["ПМ12", "ПМЛ", "КМИ", "КТП", "КТ-"]
    },
    # 3. Relays, buttons, lamps (Реле, кнопки, сигнальные лампы)
    {
        "category": "Реле / Кнопка / Лампа",
        "regex": re.compile(
            r"(?:реле|кнопка|лампа\s+сигнальная|арматура\s+сигнальная|HL\d*|SB\d*)\s+([\w\d\-]+(?:\s+[\w\d\-]+)*)",
            re.IGNORECASE
        ),
        "fallback_keywords": ["РНПП", "РЭК", "РП-", "РТЛ", "РТИ", "ЛС-", "AL-22", "AD22", "КИП"]
    },
    # 4. Terminals, busbars (Клеммы, шины)
    {
        "category": "Клеммы / Шины",
        "regex": re.compile(
            r"(?:клемма|шин[аы]|нулевая\s+шина|шина\s+медная|cross-block|кросс-модуль)\s+([\w\d\-]+(?:\s+[\w\d\-]+)*)",
            re.IGNORECASE
        ),
        "fallback_keywords": ["ШНИ", "ШНИ-", "PEN", "КЕ-", "кросс-модуль", "Cross"]
    },
    # 5. Cabinets, Enclosures (Шкафы, щиты, оболочки)
    {
        "category": "Корпус / Шкаф",
        "regex": re.compile(
            r"(?:шкаф|щит|корпус|оболочка|ШЭМ|ЩМП)\s+([\w\d\-]+(?:\s+[\w\d\-]+)*)",
            re.IGNORECASE
        ),
        "fallback_keywords": ["ШЭМ", "ЩМП", "ЩРн", "ЩРв", "КСР", "КСР-"]
    }
]

# Quantity extraction regex pattern
# Finds numbers followed by qty unit or standalone column digits.
QTY_PATTERN = re.compile(
    r"(?:кол-во|количество|кол\.?|x|х)?\s*(\d+)\s*(?:шт\.?|ед\.?|компл\.?|м(?:\s|$)|\-|$)",
    re.IGNORECASE
)

def analyze_equipment(text: str) -> List[Dict[str, Any]]:
    """
    Parses full text line-by-line using heuristics and regex to discover BOM items.
    """
    if not text:
        return []

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    equipment_list = []
    item_id = 1

    for line in lines:
        matched = False

        # Look for category pattern matches in the line
        for p in PATTERNS:
            match = p["regex"].search(line)
            found_name = None
            category = p["category"]

            if match:
                # Get non-empty group
                groups = [g for g in match.groups() if g]
                if groups:
                    found_name = f"{category} {groups[0].strip()}"
            else:
                # Check fallback keywords
                for kw in p["fallback_keywords"]:
                    if kw.lower() in line.lower():
                        # Extract context around the keyword
                        found_name = f"{category} {kw}"
                        # Try to capture more detail
                        kw_idx = line.lower().find(kw.lower())
                        segment = line[kw_idx:kw_idx+35].strip()
                        if len(segment) > len(kw):
                            found_name = segment
                        break

            if found_name:
                # Try to extract quantity
                qty = 1
                qty_matches = QTY_PATTERN.findall(line)
                if qty_matches:
                    # Clean quantities & find the most likely count (usually the last number before unit or standalone digits)
                    for q in qty_matches:
                        if q.isdigit() and int(q) > 0:
                            qty = int(q)

                # Deduplicate or add
                # Clean up extracted name
                found_name = re.sub(r'\s+', ' ', found_name).strip()
                # Ensure it doesn't contain too many generic words or characters
                if len(found_name) > 100:
                    found_name = found_name[:100] + "..."

                # Determine unit
                unit = "шт"
                if "м" in line.lower() and "кабель" in line.lower():
                    unit = "м"

                equipment_list.append({
                    "id": item_id,
                    "name": found_name,
                    "qty": qty,
                    "unit": unit
                })
                item_id += 1
                matched = True
                break

    # If no items were parsed but text exists, return a default template/fallback parsing
    if not equipment_list:
        # Check standard text occurrences to construct a friendly default table or parse general quantities
        # E.g. search for numbers
        pass

    return equipment_list
