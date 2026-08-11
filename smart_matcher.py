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
        Ищет по: серия + номинал + полюсность
        """
        series = str(detected.get('series') or '')
        nominal = str(detected.get('nominal') or '')
        poles = str(detected.get('poles') or '')

        # Извлекаем числовой номинал
        amp = self._extract_amperage(nominal)
        if not amp:
            return None, 0.0

        candidates = []

        # 1. ТОЧНОЕ СОВПАДЕНИЕ: серия + номинал + полюсность
        if series:
            for item in self.price_list:
                item_name = item.get('Наименование', '')
                # Очищаем серию от модификаторов (EN, TM, EM)
                clean_series = series.replace(' EN', '').replace(' TM', '').replace(' EM', '')
                if clean_series in item_name:
                    item_amp = self._extract_amperage(item_name)
                    if item_amp == amp:
                        if not poles or poles in item_name:
                            return item, 1.0

        # 2. БАЗОВАЯ СЕРИЯ (без модификаторов) + номинал
        if series:
            base_series = series.split('-')[0] if '-' in series else series
            for item in self.price_list:
                item_name = item.get('Наименование', '')
                if base_series in item_name:
                    item_amp = self._extract_amperage(item_name)
                    if item_amp == amp:
                        if not poles or poles in item_name:
                            candidates.append((item, 0.8))

        # 3. ТИП + НОМИНАЛ + ПОЛЮСНОСТЬ
        item_type = str(detected.get('type') or 'автомат')
        for item in self.price_list:
            item_name = item.get('Наименование', '')
            if 'Авт. выкл.' in item_name or 'автомат' in item_name.lower():
                item_amp = self._extract_amperage(item_name)
                if item_amp == amp:
                    if not poles or poles in item_name:
                        candidates.append((item, 0.5))

        # 4. ТОЛЬКО НОМИНАЛ + ПОЛЮСНОСТЬ (самый широкий поиск)
        for item in self.price_list:
            item_name = item.get('Наименование', '')
            item_amp = self._extract_amperage(item_name)
            if item_amp == amp:
                if not poles or poles in item_name:
                    candidates.append((item, 0.3))

        if candidates:
            # Сортируем по уверенности и убираем дубли
            candidates.sort(key=lambda x: x[1], reverse=True)
            seen = set()
            unique = []
            for item, score in candidates:
                article = item.get('Артикул')
                if article not in seen:
                    seen.add(article)
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
