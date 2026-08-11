import re
from typing import Dict, List, Optional, Tuple
from difflib import SequenceMatcher

class SmartMatcher:
    def __init__(self, price_list: List[Dict]):
        self.price_list = price_list
        self._build_index()

    def _build_index(self):
        """Строит оптимизированный индекс для поиска"""
        self.index = {
            'by_series': {},
            'by_type': {},
            'by_amp': {},
            'by_keyword': {},
            'by_article': {}
        }

        for item in self.price_list:
            name = item.get('Наименование', '')
            article = str(item.get('Артикул', ''))

            # Извлекаем параметры
            series = self._extract_series(name)
            item_type = self._extract_type(name)
            amp = self._extract_amperage(name)
            keywords = self._extract_keywords(name)

            # Индексируем
            if series:
                self.index['by_series'].setdefault(series, []).append(item)
            if item_type:
                self.index['by_type'].setdefault(item_type, []).append(item)
            if amp:
                self.index['by_amp'].setdefault(amp, []).append(item)
            if article:
                self.index['by_article'][article] = item
            for kw in keywords:
                self.index['by_keyword'].setdefault(kw, []).append(item)

    def _infer_series(self, poles: str, amperage: int) -> str:
        """Определяет серию на основе полюсности и номинала"""

        # 3P автоматы
        if poles == "3P":
            if amperage >= 800:
                return "NM8N-1600Q EN 3P"
            elif amperage >= 250:
                return "NM8N-250S EN 3P"
            elif amperage >= 125:
                return "NM8N-250S EN 3P"
            elif amperage >= 63:
                return "NM8N-250S EN 3P"
            else:
                return "NB2-40ZT 3P"

        # 1P автоматы
        elif poles == "1P":
            if amperage >= 63:
                return "NB2-80ZT 1P"
            elif amperage >= 40:
                return "NB2-40ZT 1P"
            elif amperage >= 16:
                return "NB2-40ZT 1P"
            else:
                return "NB2-40ZT 1P"

        # 4P автоматы
        elif poles == "4P":
            if amperage >= 800:
                return "NM8N-1600Q EN 4P"
            elif amperage >= 250:
                return "NM8N-250S EN 4P"
            else:
                return "NM8N-250S EN 4P"

        return None

    def match(self, detected: Dict) -> Tuple[Optional[Dict], float]:
        """Сопоставляет с прайсом с автоопределением серии"""

        series = str(detected.get('series') or '')
        nominal = str(detected.get('nominal') or '')
        poles = str(detected.get('poles') or '')

        # Извлекаем номинал
        amp = self._extract_amperage(nominal)
        if not amp:
            return None, 0.0

        # Если серия не определена - определяем по правилам
        if not series:
            series = self._infer_series(poles, amp)
            if not series:
                return None, 0.0

        # Ищем в прайсе
        for item in self.price_list:
            item_name = item.get('Наименование', '')
            # Проверяем серию (очищаем от модификаторов)
            clean_series = series.replace(' EN', '').replace(' TM', '').replace(' EM', '')
            if clean_series in item_name:
                item_amp = self._extract_amperage(item_name)
                if item_amp == amp:
                    return item, 1.0

        # Если не нашли - пробуем по базовой серии
        base_series = series.split('-')[0] if '-' in series else series
        for item in self.price_list:
            item_name = item.get('Наименование', '')
            if base_series in item_name:
                item_amp = self._extract_amperage(item_name)
                if item_amp == amp:
                    return item, 0.8

        return None, 0.0

    def _extract_series(self, name: str) -> Optional[str]:
        patterns = [
            r'(NM8[N,S]-\d+[A-Z])',
            r'(NVF7-\d+[A-Z])',
            r'(NKB1-\d+)',
            r'(NB[2,8]-\d+[A-Z])',
            r'(NC[1,8]-\d+)',
            r'(NR[8,E]-\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, name)
            if match:
                return match.group(0)
        return None

    def _extract_type(self, name: str) -> Optional[str]:
        types = {
            'автомат': ['Авт. выкл.', 'автоматический'],
            'преобразователь': ['Преобразователь', 'NVF7'],
            'пускатель': ['Пускатель', 'NKB1'],
            'контактор': ['Контактор', 'NC'],
            'реле': ['Реле', 'NR'],
            'блок': ['Блок', 'адаптер'],
        }
        for type_name, keywords in types.items():
            if any(kw in name for kw in keywords):
                return type_name
        return None

    def _extract_amperage(self, text: str) -> Optional[int]:
        match = re.search(r'(\d+)\s*[АA]', str(text))
        if match:
            return int(match.group(1))
        return None

    def _extract_keywords(self, name: str) -> List[str]:
        keywords = []
        nums = re.findall(r'(\d+)\s*[АA]', name)
        for n in nums:
            keywords.append(f"{n}А")
        poles = re.findall(r'(\d)P', name)
        for p in poles:
            keywords.append(f"{p}P")
        return keywords
