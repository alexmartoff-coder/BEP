import os
import json
import hashlib
import logging
import asyncio
import base64
import io
from typing import List, Dict, Any
from pdf2image import convert_from_path
from openai import OpenAI

logger = logging.getLogger("vision_parser")

CACHE_FILE = "/tmp/gemini_vision_cache.json"
_memory_cache: Dict[str, List[Dict[str, Any]]] = {}

def load_cache():
    global _memory_cache
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                _memory_cache = json.load(f)
            logger.info(f"[Vision] Loaded {len(_memory_cache)} cached items from {CACHE_FILE}")
        except Exception as e:
            logger.warning(f"[Vision] Failed to load vision cache from file: {e}")
            _memory_cache = {}

def save_cache():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_memory_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[Vision] Failed to save vision cache to file: {e}")

# Initial load
load_cache()

def compute_pdf_md5(pdf_path: str) -> str:
    """Computes the MD5 checksum of a file to use as a unique cache key."""
    hash_md5 = hashlib.md5()
    try:
        with open(pdf_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        logger.error(f"[Vision] Error computing MD5 of {pdf_path}: {e}")
        # Fall back to file path and size if MD5 fails
        return f"{pdf_path}_{os.path.getsize(pdf_path) if os.path.exists(pdf_path) else 0}"

def pil_image_to_base64(img) -> str:
    """Converts a PIL Image to a base64 encoded PNG string."""
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

def clean_json_response(text: str) -> str:
    """Strips markdown code blocks and clean whitespaces to extract raw JSON."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text

async def parse_equipment_from_pdf(pdf_path: str) -> List[Dict[str, Any]]:
    """
    Extracts equipment from PDF using OpenRouter API with Qwen VL Plus model (primary)
    and Gemma 4 (fallback if 404).
    Converts PDF pages into PIL images, sends them as base64 data URLs,
    and returns a parsed list of equipment dictionaries.
    """
    # 1. Check cache first
    file_hash = compute_pdf_md5(pdf_path)
    if file_hash in _memory_cache:
        logger.info(f"[Vision] Cache hit for PDF {pdf_path} (MD5: {file_hash}). Returning cached data.")
        return _memory_cache[file_hash]

    # 2. Check API Key
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        logger.error("[Vision] OPENROUTER_API_KEY environment variable is not set. Skipping OpenRouter Vision parsing.")
        return []

    try:
        # Initialize OpenAI client pointed to OpenRouter
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key
        )

        # Convert PDF to PIL images in an executor to avoid blocking the event loop
        logger.info(f"[Vision] Converting PDF {pdf_path} to images...")
        images = await asyncio.to_thread(convert_from_path, pdf_path, dpi=150)

        if not images:
            logger.warning(f"[Vision] No pages extracted from PDF: {pdf_path}")
            return []

        # Limit pages to avoid huge payloads (e.g. max first 10 pages)
        images_to_send = images[:10]
        if len(images) > 10:
            logger.info(f"[Vision] PDF has {len(images)} pages. Limiting Vision API processing to first 10 pages.")

        # Prepare system instructions / prompt
        prompt = """
Ты — ассистент по распознаванию электрощитового оборудования из проектной документации.

В PDF есть разные разделы: общие данные, описания, схемы, таблицы с характеристиками ДГУ.

Твоя задача — найти и извлечь ТОЛЬКО оборудование, которое входит в коммерческое предложение.

Игнорируй:
- Текстовые описания задач (например, "Настроить АВР...")
- Строки с L1-L2-L3
- Технические расчёты (сопротивление заземления, формулы)
- Общие данные о проекте
- Строки с нулевыми ценами

Ищи:
- Артикулы в формате: CHINT-XXX, АД-500С-Т400-2РНМ9, или любые буквенно-цифровые коды
- Названия оборудования: "Дизель генератор", "Автоматический выключатель", "Контактор", "УЗО", "Шкаф", "ДГУ"
- Количество (цифры рядом с названием)
- Единицу измерения: "шт", "компл", "м"

Выведи результат СТРОГО в формате JSON-массива без какого-либо дополнительного текста, markdown-разметки или объяснений:
[
  {"article": "АД-500С-Т400-2РНМ9", "name": "Дизель генератор 500 кВт", "qty": 1, "unit": "шт"},
  {"article": "Evotec TCU368D", "name": "Генератор Evotec 500 кВт", "qty": 1, "unit": "шт"}
]

Если сомневаешься — пропускай позицию. Лучше меньше, но точнее.
"""
        # Format payload in OpenAI Vision API format
        content_payload = [{"type": "text", "text": prompt}]
        for img in images_to_send:
            b64_str = pil_image_to_base64(img)
            content_payload.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{b64_str}"
                }
            })

        # Try primary model first
        try:
            logger.info(f"[Vision] Sending request to OpenRouter + qwen-vl-plus with {len(images_to_send)} images...")
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model="qwen/qwen-vl-plus:free",
                messages=[
                    {
                        "role": "user",
                        "content": content_payload
                    }
                ],
                temperature=0.1
            )
        except Exception as e:
            # Check for 404 Not Found error to invoke fallback
            is_404 = False
            if hasattr(e, "status_code") and e.status_code == 404:
                is_404 = True
            elif "404" in str(e) or "NOT_FOUND" in str(e).upper():
                is_404 = True

            if is_404:
                logger.warning(f"[Vision] Primary model qwen/qwen-vl-plus:free returned 404. Attempting fallback model google/gemma-4-31b-it:free...")
                response = await asyncio.to_thread(
                    client.chat.completions.create,
                    model="google/gemma-4-31b-it:free",
                    messages=[
                        {
                            "role": "user",
                            "content": content_payload
                        }
                    ],
                    temperature=0.1
                )
            else:
                raise e

        if not response or not response.choices or not response.choices[0].message.content:
            logger.warning("[Vision] OpenRouter API returned an empty response.")
            return []

        # Parse output JSON
        raw_text = response.choices[0].message.content
        cleaned_text = clean_json_response(raw_text)
        logger.debug(f"Raw response from OpenRouter: {cleaned_text}")

        parsed_data = json.loads(cleaned_text)
        if not isinstance(parsed_data, list):
            logger.warning("[Vision] OpenRouter response is not a list. Attempting to wrap it.")
            if isinstance(parsed_data, dict) and "items" in parsed_data:
                parsed_data = parsed_data["items"]
            else:
                parsed_data = [parsed_data]

        # Standardize results
        standardized_items = []
        for item in parsed_data:
            if not isinstance(item, dict):
                continue
            article = str(item.get("article", "")).strip()
            name = str(item.get("name", "")).strip()
            qty = item.get("qty", 1)
            try:
                qty = int(qty)
            except (ValueError, TypeError):
                qty = 1
            unit = str(item.get("unit", "шт")).strip()

            if article or name:
                standardized_items.append({
                    "article": article,
                    "name": name,
                    "qty": qty,
                    "unit": unit
                })

        logger.info(f"[Vision] Successfully parsed {len(standardized_items)} items using OpenRouter.")

        # Store to cache
        if standardized_items:
            _memory_cache[file_hash] = standardized_items
            save_cache()

        return standardized_items

    except Exception as e:
        logger.error(f"[Vision] OpenRouter Vision API parsing failed: {e}", exc_info=True)
        # Log error and return empty list to not break the pipeline
        return []
