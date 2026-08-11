from typing import Dict, List

class PromptGenerator:
    def __init__(self, analysis: Dict):
        self.analysis = analysis

    def generate(self) -> str:
        """
        Генерирует промпт на основе анализа прайса
        """
        # Подготовка данных
        series_list = ', '.join(self.analysis['series'][:10])
        if len(self.analysis['series']) > 10:
            series_list += f" и {len(self.analysis['series']) - 10} других"

        type_list = ', '.join(self.analysis['types'])
        amp_min, amp_max = self.analysis['amp_range']
        poles_list = ', '.join(self.analysis['poles']) if self.analysis['poles'] else 'различная'

        # Формируем промпт
        prompt = f"""Ты — инженер-сметчик по электротехническому оборудованию.

Проанализируй электрическую схему и найди ВСЕ элементы оборудования.

На основе прайс-листа определены следующие категории:
- Типы устройств: {type_list}
- Серии: {series_list}
- Номиналы: от {amp_min}А до {amp_max}А
- Полюсность: {poles_list}

Для каждого найденного элемента укажи:
- "mark": обозначение на схеме (QF1, QF2, KM1, ...)
- "type": тип устройства (одно из: {type_list})
- "series": серия (если видна на схеме)
- "specs": характеристики (полюсность, номинал, доп. опции)
- "nominal": номинальный ток в Амперах (если виден)

ВАЖНО:
1. Отвечай ТОЛЬКО в формате JSON массива.
2. Если элемент не удается идентифицировать - пропускай его.
3. Старайся максимально точно определить серию и характеристики.
4. Не пиши НИКАКОГО текста, кроме JSON.

Формат: [
  {{"mark":"QF1","type":"автомат","series":"NM8N-1600Q","specs":"3P 1250A","nominal":"1250A"}},
  {{"mark":"KM1","type":"контактор","series":"NC8-100","specs":"3P 100A","nominal":"100A"}}
]"""
        return prompt
