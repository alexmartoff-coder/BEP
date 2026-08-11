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

    def match(self, detected: Dict) -> Tuple[Optional[Dict], float]:
        """
        Сопоставляет распознанный элемент с прайсом
        Возвращает: (позиция_из_прайса, уверенность_совпадения)
        """
        candidates = []

        # 1. Поиск по серии + номиналу (точное совпадение)
        series = detected.get('series')
        nominal = detected.get('nominal')

        if series and nominal:
            amp = self._extract_amperage(nominal)
            if amp and series in self.index['by_series']:
                for item in self.index['by_series'][series]:
                    item_amp = self._extract_amperage(item['Наименование'])
                    if item_amp == amp:
                        return item, 1.0

        # 2. Поиск по типу + номиналу
        item_type = detected.get('type')
        if item_type and nominal:
            amp = self._extract_amperage(nominal)
            if amp and item_type in self.index['by_type']:
                for item in self.index['by_type'][item_type]:
                    item_amp = self._extract_amperage(item['Наименование'])
                    if item_amp and abs(item_amp - amp) <= 10:  # допуск 10А
                        candidates.append((item, 0.8))

        # 3. Поиск по ключевым словам
        if not candidates:
            name = detected.get('name', detected.get('mark', ''))
            keywords = self._extract_keywords(name)
            for kw in keywords:
                if kw in self.index['by_keyword']:
                    for item in self.index['by_keyword'][kw]:
                        # Проверяем соответствие по типу
                        if item_type and self._extract_type(item['Наименование']) == item_type:
                            candidates.append((item, 0.7))
                        else:
                            candidates.append((item, 0.5))

        # 4. Поиск по частичному совпадению названия
        if not candidates and detected.get('name'):
            search_name = detected['name'].lower()
            for item in self.price_list:
                item_name = item.get('Наименование', '').lower()
                if search_name in item_name or item_name in search_name:
                    similarity = SequenceMatcher(None, search_name, item_name).ratio()
                    if similarity > 0.6:
                        candidates.append((item, similarity))

        if candidates:
            # Сортируем по уверенности
            candidates.sort(key=lambda x: x[1], reverse=True)
            # Убираем дубликаты
            unique = []
            seen = set()
            for item, score in candidates:
                if item.get('Артикул') not in seen:
                    seen.add(item.get('Артикул'))
                    unique.append((item, score))
            return unique[0] if unique else (None, 0.0)

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
