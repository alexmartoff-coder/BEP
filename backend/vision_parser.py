import os
import json
import hashlib
import logging
import asyncio
from typing import List, Dict, Any
from pdf2image import convert_from_path
import google.generativeai as genai

logger = logging.getLogger("vision_parser")

CACHE_FILE = "/tmp/gemini_vision_cache.json"
_memory_cache: Dict[str, List[Dict[str, Any]]] = {}

def load_cache():
    global _memory_cache
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                _memory_cache = json.load(f)
            logger.info(f"Loaded {len(_memory_cache)} cached items from {CACHE_FILE}")
        except Exception as e:
            logger.warning(f"Failed to load vision cache from file: {e}")
            _memory_cache = {}

def save_cache():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_memory_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save vision cache to file: {e}")

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
        logger.error(f"Error computing MD5 of {pdf_path}: {e}")
        # Fall back to file path and size if MD5 fails
        return f"{pdf_path}_{os.path.getsize(pdf_path) if os.path.exists(pdf_path) else 0}"

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
    Extracts equipment from PDF using Gemini Vision API.
    Converts PDF pages into PIL images, sends them to gemini-1.5-flash with a structured prompt,
    and returns a parsed list of equipment dictionaries.
    """
    # 1. Check cache first
    file_hash = compute_pdf_md5(pdf_path)
    if file_hash in _memory_cache:
        logger.info(f"Cache hit for PDF {pdf_path} (MD5: {file_hash}). Returning cached data.")
        return _memory_cache[file_hash]

    # 2. Check API Key
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        logger.error("GOOGLE_API_KEY environment variable is not set. Skipping Gemini Vision parsing.")
        return []

    try:
        # Configure Gemini API
        genai.configure(api_key=api_key)

        # Convert PDF to PIL images in an executor to avoid blocking the event loop
        logger.info(f"Converting PDF {pdf_path} to images...")
        images = await asyncio.to_thread(convert_from_path, pdf_path, dpi=150)

        if not images:
            logger.warning(f"No pages extracted from PDF: {pdf_path}")
            return []

        # We can limit the number of pages to avoid hitting payload size/token limits (e.g. max first 10 pages)
        # 10 pages is generally plenty for schematic spec sheets.
        images_to_send = images[:10]
        if len(images) > 10:
            logger.info(f"PDF has {len(images)} pages. Limiting Vision API processing to first 10 pages.")

        # Prepare multimodal request prompt
        prompt = (
            "Вы — эксперт по чтению электрических схем и спецификаций оборудования.\n"
            "Распознайте и извлеките все элементы оборудования (автоматические выключатели, контакторы, реле, рубильники, корпуса и т.д.) с предоставленных страниц PDF-документа/чертежей.\n"
            "Для каждого найденного элемента определите:\n"
            "- article: Артикул или заказной код (если указан, иначе пустая строка \"\"). Очистите артикул от пробелов по краям.\n"
            "- name: Полноценное понятное наименование (например, \"Автоматический выключатель 3P 16A\" или \"Контактор 18А 230В\"). Не сокращайте технические параметры.\n"
            "- qty: Количество (целое число >= 1).\n"
            "- unit: Единица измерения (например, \"шт\").\n\n"
            "Верните результат СТРОГО в формате JSON-массива объектов, содержащих эти 4 поля.\n"
            "Пример вывода:\n"
            "[\n"
            "  {\"article\": \"A9F74316\", \"name\": \"Автоматический выключатель iC60N 3P 16A B\", \"qty\": 2, \"unit\": \"шт\"},\n"
            "  {\"article\": \"NC8-1810\", \"name\": \"Контактор nc8 18А\", \"qty\": 1, \"unit\": \"шт\"}\n"
            "]\n\n"
            "Не добавляйте никакого дополнительного текста, разметки markdown или объяснений. Только чистый JSON."
        )

        model = genai.GenerativeModel("gemini-1.5-flash")

        # Prepare contents: prompt followed by images
        contents = [prompt] + images_to_send

        logger.info(f"Sending request to gemini-1.5-flash with {len(images_to_send)} images...")

        # Run API call with structured JSON response config and 90s timeout
        generation_config = {"response_mime_type": "application/json"}

        response = await asyncio.to_thread(
            model.generate_content,
            contents,
            generation_config=generation_config
        )

        if not response or not response.text:
            logger.warning("Gemini Vision API returned an empty response.")
            return []

        # Parse output JSON
        cleaned_text = clean_json_response(response.text)
        logger.debug(f"Raw response from Gemini Vision: {cleaned_text}")

        parsed_data = json.loads(cleaned_text)
        if not isinstance(parsed_data, list):
            logger.warning("Gemini response is not a list. Attempting to wrap it.")
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

        logger.info(f"Successfully parsed {len(standardized_items)} items from Gemini Vision API.")

        # Store to cache
        if standardized_items:
            _memory_cache[file_hash] = standardized_items
            save_cache()

        return standardized_items

    except Exception as e:
        logger.error(f"Gemini Vision API parsing failed: {e}", exc_info=True)
        # Log error and return empty list to not break the pipeline
        return []
