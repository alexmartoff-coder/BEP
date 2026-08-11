# prompt_generator.py

from typing import Dict, List

class PromptGenerator:
    def __init__(self, analysis: Dict):
        self.analysis = analysis

    def generate(self) -> str:
        """Генерирует промпт с полным списком серий из прайса"""

        # Получаем все серии из анализа
        all_series = self.analysis.get('series', [])

        # Группируем серии для удобства
        series_by_type = self._group_series(all_series)

        # Формируем читаемый список серий
        series_text = self._format_series_list(series_by_type)

        prompt = f"""Ты — инженер-сметчик по электротехническому оборудованию CHINT.

Проанализируй электрическую схему и найди ВСЕ автоматические выключатели.

ВОЗМОЖНЫЕ СЕРИИ (ТОЛЬКО ИЗ ЭТОГО СПИСКА):
{series_text}

ПРАВИЛА ОПРЕДЕЛЕНИЯ СЕРИЙ:
1. Если автомат 3P и ток 125А — скорее всего NM8N-250S или NM8N-250Q
2. Если автомат 1P и ток 63А — скорее всего NB2-40ZT или NB2-80ZT
3. Если автомат 1P и ток 16А — скорее всего NB2-40ZT
4. Если автомат 3P и ток 63А — скорее всего NM8N-250S или NB2-80ZT

Для каждого найденного автомата определи:
- "mark": обозначение на схеме (QF1, QF2, ...)
- "series": ТОЧНАЯ СЕРИЯ из списка выше
- "nominal": номинальный ток (125A, 63A, 16A)
- "poles": количество полюсов (3P, 1P)

ВАЖНО:
1. Используй ТОЛЬКО серии из списка выше
2. Если сомневаешься — выбери наиболее вероятную серию
3. Отвечай ТОЛЬКО в формате JSON массива

ФОРМАТ:
[
  {{"mark":"QF1","series":"NM8N-250S EN 3P","nominal":"125A","poles":"3P"}},
  {{"mark":"QF7","series":"NB2-40ZT 1P","nominal":"63A","poles":"1P"}}
]

НЕ ПИШИ НИКАКОГО ТЕКСТА, КРОМЕ JSON!"""

        return prompt

    def _group_series(self, series_list: List[str]) -> Dict[str, List[str]]:
        """Группирует серии по семействам"""
        groups = {
            'NM8N (до 1600А)': [],
            'NM8S (до 1600А)': [],
            'NB2 (до 80А)': [],
            'NB8 (до 125А)': [],
            'Другое': []
        }

        for s in series_list:
            if s.startswith('NM8N'):
                groups['NM8N (до 1600А)'].append(s)
            elif s.startswith('NM8S'):
                groups['NM8S (до 1600А)'].append(s)
            elif s.startswith('NB2'):
                groups['NB2 (до 80А)'].append(s)
            elif s.startswith('NB8'):
                groups['NB8 (до 125А)'].append(s)
            else:
                groups['Другое'].append(s)

        # Удаляем пустые группы
        return {k: v for k, v in groups.items() if v}

    def _format_series_list(self, series_by_type: Dict[str, List[str]]) -> str:
        """Форматирует список серий для промпта"""
        lines = []
        for type_name, series_list in series_by_type.items():
            lines.append(f"  {type_name}:")
            for s in series_list[:5]:  # Показываем первые 5 для краткости
                lines.append(f"    - {s}")
            if len(series_list) > 5:
                lines.append(f"    - ... и ещё {len(series_list) - 5}")
        return '\n'.join(lines)
