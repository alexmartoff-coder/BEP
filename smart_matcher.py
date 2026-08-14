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

    def _infer_series(self, poles: str, amperage: int, device_type: str = "") -> str:
        """Определяет серию на основе полюсности, номинала и типа устройства"""
        dt_clean = str(device_type or "").lower().strip()

        # 1. Рубильники (QS) -> серия NH4 или DZ158
        if "рубильник" in dt_clean or "выключатель нагрузки" in dt_clean:
            return "NH4"

        # 2. УЗО (QD) -> серия NL1
        if "узо" in dt_clean:
            return "NL1"

        # 3. Дифавтоматы (QFD) -> серия NB1L
        if "дифавтомат" in dt_clean or "авдт" in dt_clean:
            return "NB1L"

        # 4. Контакторы (KM) -> серия NC1 или NC8
        if "контактор" in dt_clean:
            return "NC8"

        # 5. Преобразователи частоты (U) -> серия NVF7
        if "преобразователь" in dt_clean or "частотник" in dt_clean:
            return "NVF7"

        # 3P автоматы (QF)
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

        # 1P автоматы (QF)
        elif poles == "1P":
            if amperage >= 63:
                return "NB2-80ZT 1P"
            elif amperage >= 40:
                return "NB2-40ZT 1P"
            elif amperage >= 16:
                return "NB2-40ZT 1P"
            else:
                return "NB2-40ZT 1P"

        # 4P автоматы (QF)
        elif poles == "4P":
            if amperage >= 800:
                return "NM8N-1600Q EN 4P"
            elif amperage >= 250:
                return "NM8N-250S EN 4P"
            else:
                return "NM8N-250S EN 4P"

        return None

    def match(self, detected: Dict) -> Tuple[Optional[Dict], float]:
        """Сопоставляет с прайсом с автоопределением серии и сверх-устойчивым поиском"""

        series = str(detected.get('series') or detected.get('name') or '')
        nominal = str(detected.get('nominal') or detected.get('current_a') or '')
        poles = str(detected.get('poles') or '').upper().strip()
        dt = str(detected.get('type') or '')

        # Извлекаем номинал
        amp = self._extract_amperage(nominal)
        if not amp:
            current_a_val = detected.get('current_a')
            if current_a_val:
                try:
                    amp = int(re.search(r'\d+', str(current_a_val)).group(0))
                except Exception:
                    pass
        if not amp:
            return None, 0.0

        # Нормализуем poles
        poles_norm = poles
        if poles_norm:
            poles_norm = poles_norm.replace('П', 'P').replace('ПОЛЮС', 'P').replace(' ', '')

        # Извлекаем базовую серию
        base_series = None
        for item_series in [series] + [self._extract_series(series) or ""]:
            if not item_series:
                continue
            m = re.search(r'\b(NM8[N,S]|NB[2,8]|NC[1,8]|NVF7|NKB1|NR8|NRE8|NH4|NL1|NB1L)\b', item_series, re.IGNORECASE)
            if m:
                base_series = m.group(1).upper()
                break

        if not base_series:
            inferred = self._infer_series(poles_norm, amp, device_type=dt)
            if inferred:
                m = re.search(r'\b(NM8[N,S]|NB[2,8]|NC[1,8]|NVF7|NKB1|NR8|NRE8|NH4|NL1|NB1L)\b', inferred, re.IGNORECASE)
                if m:
                    base_series = m.group(1).upper()

        candidates = []
        for item in self.price_list:
            item_name = item.get('Наименование', '')

            # 1. Извлекаем номинал позиции прайса
            item_amp = self._extract_amperage(item_name)
            if item_amp != amp:
                continue

            # 2. Извлекаем poles из названия в прайсе
            item_poles = None
            p_match = re.search(r'\b([1-4])\s*(?:P|П|полюс|п|p)\b', item_name, re.IGNORECASE)
            if p_match:
                item_poles = f"{p_match.group(1)}P"

            if poles_norm and item_poles and poles_norm != item_poles:
                continue

            score = 0.5
            if base_series:
                if base_series.lower() in item_name.lower():
                    score += 0.4
                    # Дополнительный бонус за точное совпадение полной серии
                    full_series = series.split(' ')[0] if series else ""
                    if len(full_series) > 4 and full_series.lower() in item_name.lower():
                        score += 0.1

            candidates.append((item, score))

        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_item, best_score = candidates[0]
            if best_score >= 0.6:
                return best_item, best_score

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
