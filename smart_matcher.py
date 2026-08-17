import re
from typing import Dict, List, Optional, Tuple
from difflib import SequenceMatcher
from backend.price_parser import extract_current_a_from_name, clean_key

def get_device_category(text: str) -> str:
    """Определяет укрупненную категорию оборудования для предотвращения некорректных сопоставлений"""
    s = str(text or '').lower()
    if any(k in s for k in ['преобразователь', 'частотник', 'vfd', 'nvf']):
        return 'vfd'
    if any(k in s for k in ['контактор', 'пускатель', 'km', 'nc1', 'nc2', 'nc7', 'nc8', 'nkb']):
        return 'contactor'
    if any(k in s for k in ['рубильник', 'выключатель нагрузки', 'qs', 'nh4']):
        return 'disconnector'
    if any(k in s for k in ['узо', 'дифавтомат', 'авдт', 'qd', 'qfd', 'nl1', 'nb1l']):
        return 'rccb'
    if any(k in s for k in ['авт. выкл', 'автоматический выключатель', 'автомат', 'mcb', 'mccb', 'qf', 'nm8', 'nxm', 'nxb', 'nb', 'dz158', 'nm1']):
        return 'breaker'
    return 'unknown'

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
                clean_art = clean_key(article)
                if clean_art:
                    self.index['by_article'][clean_art] = item
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

        det_art = str(detected.get('article') or detected.get('mark') or '').strip()
        det_name = str(detected.get('name') or '')
        series = str(detected.get('series') or detected.get('name') or '')
        nominal = str(detected.get('nominal') or detected.get('current_a') or '')
        poles = str(detected.get('poles') or '').upper().strip()
        dt = str(detected.get('type') or '')

        # 0. Article direct matching check
        if det_art and not re.match(r'(?i)^QF\d+$', det_art):
            clean_det_art = clean_key(det_art)
            if clean_det_art in self.index['by_article']:
                return self.index['by_article'][clean_det_art], 1.0
            for item in self.price_list:
                item_art = str(item.get('Артикул') or item.get('article') or '').strip()
                if item_art and clean_key(item_art) == clean_det_art:
                    return item, 1.0

        # Извлекаем номинал
        amp = self._extract_amperage(nominal)
        if not amp:
            amp = self._extract_amperage(det_name)
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
        for item_series in [series, det_name]:
            if not item_series:
                continue
            m = re.search(r'\b(NM8[N,S]|NXM|NM1|NXB|NB[1,2,8]|NC[1,2,7,8]|NVF[2,5,7]|NZ7|NKB1|DZ158|NR[8,E]|NL1|NH4|ND2|NB1L)\b', item_series, re.IGNORECASE)
            if m:
                base_series = m.group(1).upper()
                break

        if not base_series:
            inferred = self._infer_series(poles_norm, amp, device_type=dt or det_name)
            if inferred:
                m = re.search(r'\b(NM8[N,S]|NXM|NM1|NXB|NB[1,2,8]|NC[1,2,7,8]|NVF[2,5,7]|NZ7|NKB1|DZ158|NR[8,E]|NL1|NH4|ND2|NB1L)\b', inferred, re.IGNORECASE)
                if m:
                    base_series = m.group(1).upper()

        det_cat = get_device_category(' '.join([det_name, dt, series, det_art]))

        candidates = []
        det_name_lower = det_name.lower()
        has_explicit_fuse = 'предохранител' in det_name_lower or 'плавкая вставка' in det_name_lower
        is_breaker_req = any(kw in det_name_lower for kw in ['qf', 'авт', 'автомат', 'breaker']) or det_cat == 'breaker'

        candidates = []
        for item in self.price_list:
            item_name = str(item.get('Наименование') or item.get('name') or '')
            if not item_name:
                continue

            item_name_lower = item_name.lower()

            # If circuit breaker requested:
            # REJECT load disconnectors/switches (выключатель нагрузки, разъединитель, NH4, NH40, NH45) and fuses (предохранител, RT36)
            if is_breaker_req:
                is_disconnector = any(kw in item_name_lower for kw in ['выключатель нагрузки', 'разъединитель', 'nh4', 'nh40', 'nh45'])
                if is_disconnector:
                    continue
                is_fuse_item = any(kw in item_name_lower for kw in ['предохранитель', 'плавкая вставка', 'rt36', 'ппн', 'fuse'])
                if is_fuse_item and not has_explicit_fuse:
                    continue
                has_breaker_kw = 'авт. выкл' in item_name_lower or 'автоматический' in item_name_lower or any(s in item_name_lower for s in ['nb', 'nm8', 'nxb', 'nb1', 'nb2', 'nxm', 'nm1', 'dz158'])
                if not has_breaker_kw:
                    continue

            # Disallow matching circuit breaker positions to fuses/RT36/melt inserts unless explicitly requested
            is_fuse_item = any(kw in item_name_lower for kw in ['предохранитель', 'плавкая вставка', 'rt36', 'ппн', 'fuse'])
            if is_fuse_item and not has_explicit_fuse:
                continue

            # Check category conflict (e.g. breaker vs VFD)
            cand_cat = get_device_category(item_name)
            if det_cat != 'unknown' and cand_cat != 'unknown' and det_cat != cand_cat:
                continue
            # Do not match 1P single-phase current rating positions to 3P or 1P+N / 3P+N breakers if 1P is requested
            if poles_norm == '1P':
                if '3P' in item_name.upper() or '3ПОЛ' in item_name.upper():
                    continue
                # Avoid matching 1P+N or +N specific pole variants when strict 1P is requested
                if '1P+N' in item_name.upper() or '3P+N' in item_name.upper() or '+N' in item_name.upper():
                    continue

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

            # Base score when poles + amperage + category match
            score = 0.65

            if base_series:
                if base_series.lower() in item_name.lower():
                    score += 0.25
                    # Bonus for exact series frame matching
                    full_series = series.split(' ')[0] if series else ""
                    if len(full_series) > 3 and full_series.lower() in item_name.lower():
                        score += 0.1

            # Bonus if device type keywords match
            item_type = self._extract_type(item_name)
            det_type = self._extract_type(det_name or dt)
            if item_type and det_type and item_type == det_type:
                score += 0.1

            candidates.append((item, min(score, 1.0)))

        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_item, best_score = candidates[0]
            if best_score >= 0.6:
                return best_item, best_score

        return None, 0.0

    def _extract_series(self, name: str) -> Optional[str]:
        if not name:
            return None
        match = re.search(r'\b((?:NM8[N,S]|NXM|NM1|NXB|NB[1,2,8]|NC[1,2,7,8]|NVF[2,5,7]|NZ7|NKB1|DZ158|NR[8,E]|NL1|NH4|ND2)[-A-Z0-9/]*)\b', name, re.IGNORECASE)
        if match:
            return match.group(1).upper()
        return None

    def _extract_type(self, name: str) -> Optional[str]:
        types = {
            'автомат': ['Авт. выкл.', 'автоматический', 'выключатель'],
            'преобразователь': ['Преобразователь', 'NVF'],
            'пускатель': ['Пускатель', 'NKB'],
            'контактор': ['Контактор', 'NC'],
            'реле': ['Реле', 'NR'],
            'блок': ['Блок', 'адаптер'],
        }
        for type_name, keywords in types.items():
            if any(kw.lower() in name.lower() for kw in keywords):
                return type_name
        return None

    def _extract_amperage(self, text: str) -> Optional[int]:
        if not text:
            return None
        return extract_current_a_from_name(str(text))

    def _extract_keywords(self, name: str) -> List[str]:
        keywords = []
        nums = re.findall(r'(\d+)\s*[АA]', name)
        for n in nums:
            keywords.append(f"{n}А")
        poles = re.findall(r'(\d)P', name)
        for p in poles:
            keywords.append(f"{p}P")
        return keywords
