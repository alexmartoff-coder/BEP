import re
from typing import Dict, List, Set, Tuple, Optional
from collections import Counter

class PriceAnalyzer:
    def __init__(self, price_list: List[Dict]):
        self.price_list = price_list
        self.series_set = set()
        self.type_set = set()
        self.amp_range = (0, 0)
        self.poles_set = set()
        self.keywords = set()

    def analyze(self) -> Dict:
        """
        Анализирует прайс-лист и возвращает структуру данных
        """
        self._extract_series()
        self._extract_types()
        self._extract_amperage_range()
        self._extract_poles()
        self._extract_keywords()

        return {
            'series': sorted(list(self.series_set)),
            'types': sorted(list(self.type_set)),
            'amp_range': self.amp_range,
            'poles': sorted(list(self.poles_set)),
            'keywords': sorted(list(self.keywords)),
            'total_items': len(self.price_list),
            'sample_items': self.price_list[:5]
        }

    def _extract_series(self):
        """Извлекает все серии из прайса"""
        patterns = [
            r'(NM8[N,S]-\d+[A-Z])',    # NM8N-1600Q, NM8S-250H
            r'(NVF7-\d+[A-Z])',        # NVF7-7.5T
            r'(NKB1-\d+)',             # NKB1-45
            r'(NB[2,8]-\d+[A-Z])',     # NB8-125R, NB2-40ZT
            r'(NC[1,8]-\d+)',          # NC8-100, NC1-2508
            r'(NR[8,E]-\d+)',          # NR8-100, NRE8-38
            r'(NRE8-\d+)',             # NRE8-100
        ]
        for item in self.price_list:
            name = item.get('Наименование', '')
            for pattern in patterns:
                match = re.search(pattern, name)
                if match:
                    self.series_set.add(match.group(0))
                    break

    def _extract_types(self):
        """Извлекает типы устройств"""
        type_keywords = {
            'автомат': ['Авт. выкл.', 'автоматический выключатель'],
            'преобразователь': ['Преобразователь частоты', 'NVF7'],
            'пускатель': ['Интеллектуальный пускатель', 'NKB1'],
            'контактор': ['Контактор', 'NC'],
            'реле': ['Реле', 'NR', 'NRE'],
            'блок': ['Блок', 'адаптер'],
            'панель': ['панель', 'LCD'],
        }
        for item in self.price_list:
            name = item.get('Наименование', '')
            for type_name, keywords in type_keywords.items():
                if any(kw in name for kw in keywords):
                    self.type_set.add(type_name)
                    break

    def _extract_amperage_range(self):
        """Определяет диапазон номиналов"""
        amps = []
        for item in self.price_list:
            name = item.get('Наименование', '')
            match = re.search(r'(\d+)\s*[АA]', name)
            if match:
                amps.append(int(match.group(1)))
        if amps:
            self.amp_range = (min(amps), max(amps))

    def _extract_poles(self):
        """Извлекает количество полюсов"""
        for item in self.price_list:
            name = item.get('Наименование', '')
            match = re.search(r'(\d)P', name)
            if match:
                self.poles_set.add(match.group(0))

    def _extract_keywords(self):
        """Извлекает ключевые слова для поиска"""
        for item in self.price_list:
            name = item.get('Наименование', '')
            # Извлекаем числовые значения с единицами
            nums = re.findall(r'(\d+)\s*[АA]', name)
            for n in nums:
                self.keywords.add(f"{n}А")
            # Извлекаем полюсность
            poles = re.findall(r'(\d)P', name)
            for p in poles:
                self.keywords.add(f"{p}P")
