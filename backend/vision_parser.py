import os
import json
import hashlib
import logging
import asyncio
from typing import List, Dict, Any
from pdf2image import convert_from_path
from google import genai
from google.genai import types

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

async def parse_equipment_from_pdf(pdf_path: str) -> List[Dict[str, Any]]:
    """
    Extracts equipment from PDF using Gemini Vision API via google.genai SDK.
    Converts PDF pages into PIL images, sends them to gemini-1.5-flash with a structured response schema,
    and returns a parsed list of equipment dictionaries.
    """
    # 1. Check cache first
    file_hash = compute_pdf_md5(pdf_path)
    if file_hash in _memory_cache:
        logger.info(f"[Vision] Cache hit for PDF {pdf_path} (MD5: {file_hash}). Returning cached data.")
        return _memory_cache[file_hash]

    # 2. Check API Key
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        logger.error("[Vision] GOOGLE_API_KEY environment variable is not set. Skipping Gemini Vision parsing.")
        return []

    try:
        # Initialize Google GenAI client
        client = genai.Client(api_key=api_key)

        # Convert PDF to PIL images in an executor to avoid blocking the event loop
        logger.info(f"[Vision] Converting PDF {pdf_path} to images...")
        images = await asyncio.to_thread(convert_from_path, pdf_path, dpi=150)

        if not images:
            logger.warning(f"[Vision] No pages extracted from PDF: {pdf_path}")
            return []

        # We can limit the number of pages to avoid hitting payload size/token limits (e.g. max first 10 pages)
        # 10 pages is generally plenty for schematic spec sheets.
        images_to_send = images[:10]
        if len(images) > 10:
            logger.info(f"[Vision] PDF has {len(images)} pages. Limiting Vision API processing to first 10 pages.")

        # Prepare multimodal request contents: prompt and images
        prompt = (
            "Вы — эксперт по чтению электрических схем и спецификаций оборудования.\n"
            "Распознайте и извлеките все элементы оборудования (автоматические выключатели, контакторы, реле, рубильники, корпуса и т.д.) с предоставленных страниц PDF-документа/чертежей.\n"
            "Заполните поля: article (артикул), name (название), qty (количество), unit (единица измерения)."
        )
        contents = [prompt] + images_to_send

        logger.info(f"[Vision] Sending request to gemini-2.0-flash with {len(images_to_send)} images...")

        # Set up Structured Output configuration using new google.genai types
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "article": {"type": "string"},
                    "name": {"type": "string"},
                    "qty": {"type": "integer"},
                    "unit": {"type": "string"}
                },
                "required": ["article", "name", "qty", "unit"]
            }
        }

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema
        )

        # Call Generate Content in executor to avoid blocking thread
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.0-flash",
            contents=contents,
            config=config
        )

        parsed_data = response.parsed
        if not parsed_data:
            logger.warning("[Vision] Gemini Vision API returned an empty or unparsed response.")
            return []

        # Standardize results from parsed structure
        standardized_items = []
        for item in parsed_data:
            if hasattr(item, "model_dump"):
                item_dict = item.model_dump()
            elif isinstance(item, dict):
                item_dict = item
            else:
                item_dict = {
                    "article": getattr(item, "article", ""),
                    "name": getattr(item, "name", ""),
                    "qty": getattr(item, "qty", 1),
                    "unit": getattr(item, "unit", "шт")
                }

            article = str(item_dict.get("article", "")).strip()
            name = str(item_dict.get("name", "")).strip()
            qty = item_dict.get("qty", 1)
            try:
                qty = int(qty)
            except (ValueError, TypeError):
                qty = 1
            unit = str(item_dict.get("unit", "шт")).strip()

            if article or name:
                standardized_items.append({
                    "article": article,
                    "name": name,
                    "qty": qty,
                    "unit": unit
                })

        logger.info(f"[Vision] Successfully parsed {len(standardized_items)} items from Gemini Vision API.")

        # Store to cache
        if standardized_items:
            _memory_cache[file_hash] = standardized_items
            save_cache()

        return standardized_items

    except Exception as e:
        logger.error(f"[Vision] Gemini Vision API parsing failed: {e}", exc_info=True)
        # Log error and return empty list to not break the pipeline
        return []
