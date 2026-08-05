import os
import json
import hashlib
import logging
import asyncio
import base64
import io
import re
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
    Extracts equipment from PDF using OpenRouter API with google/gemma-4-26b-a4b-it:free model (primary)
    and google/gemma-4-31b-it:free (fallback if 404/429).
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

        # Prepare strict prompt as requested by the user
        prompt = """
Ты инженер по щитовому оборудованию. На изображении — однолинейная схема щита.

Извлеки ТОЛЬКО автоматические выключатели.

Смотри подписи под/рядом с автоматами: ток (100А, 125А, 400А, 630А…) и полюса (1P, 3P).

ОБЯЗАТЕЛЬНО:
- Группируй одинаковые (одинаковый ток + полюса) в ОДНУ позицию с qty
- Не пиши отдельные строки на каждый QF1, QF2…
- Игнорируй: QF, QF1, QF2, адреса, штампы, кабели, примечания, уставки, нагрузки (Сортер, Резерв)

Формат ответа — только JSON:
{
  "shield_name": "название щита",
  "items": [
    {"article": "артикул или код если указан", "name": "Авт. выкл. 3P 125А", "current_a": 125, "poles": "3P", "qty": 6}
  ]
}
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
        primary_model = "google/gemma-4-26b-a4b-it:free"
        fallback_model = "google/gemma-4-31b-it:free"

        try:
            logger.info(f"[Vision] Sending request to OpenRouter + {primary_model} with {len(images_to_send)} images...")
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=primary_model,
                messages=[
                    {
                        "role": "user",
                        "content": content_payload
                    }
                ],
                temperature=0.1
            )
        except Exception as e:
            # Check for 404 Not Found or 429 Too Many Requests to invoke fallback
            is_fallback_trigger = False
            if hasattr(e, "status_code") and e.status_code in [404, 429]:
                is_fallback_trigger = True
            elif any(indicator in str(e).upper() or indicator in str(e) for indicator in ["404", "429", "RESOURCE_EXHAUSTED", "RATE_LIMIT", "LIMIT_EXCEEDED", "NOT_FOUND"]):
                is_fallback_trigger = True

            if is_fallback_trigger:
                logger.warning(f"[Vision] Model {primary_model} failed (404/429). Attempting fallback model {fallback_model}...")
                response = await asyncio.to_thread(
                    client.chat.completions.create,
                    model=fallback_model,
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

        parsed_json = json.loads(cleaned_text)

        # Resolve parsed_json into list of items
        items_list = []
        if isinstance(parsed_json, dict):
            if "items" in parsed_json:
                items_list = parsed_json["items"]
            else:
                items_list = [parsed_json]
        elif isinstance(parsed_json, list):
            items_list = parsed_json

        # Group identical items on the backend by poles + current_a values
        grouped_dict = {}
        for item in items_list:
            if not isinstance(item, dict):
                continue

            poles = str(item.get("poles", "")).strip().upper()
            current_a = str(item.get("current_a", "")).strip()

            # Extract digits from current_a
            current_digits_match = re.search(r'\d+', current_a)
            current_val = current_digits_match.group(0) if current_digits_match else ""

            qty = item.get("qty", 1)
            try:
                qty = int(qty)
            except (ValueError, TypeError):
                qty = 1

            article = str(item.get("article", "")).strip()
            unit = str(item.get("unit", "шт")).strip()

            # Check if poles and current_val are present for grouping key
            if poles and current_val:
                group_key = (poles, current_val)
                if group_key in grouped_dict:
                    grouped_dict[group_key]["qty"] += qty
                    # Preserve article if missing
                    if article and not grouped_dict[group_key]["article"]:
                        grouped_dict[group_key]["article"] = article
                else:
                    # Form normalized name: "Авт. выкл. {poles} {current_val}А"
                    name_norm = f"Авт. выкл. {poles} {current_val}А"
                    grouped_dict[group_key] = {
                        "article": article,
                        "name": name_norm,
                        "qty": qty,
                        "unit": unit,
                        "poles": poles,
                        "current_a": current_val
                    }
            else:
                # fallback grouping by raw name
                name_raw = str(item.get("name", "Авт. выкл.")).strip()
                if not name_raw:
                    name_raw = "Авт. выкл."
                group_key = ("RAW", name_raw)
                if group_key in grouped_dict:
                    grouped_dict[group_key]["qty"] += qty
                    if article and not grouped_dict[group_key]["article"]:
                        grouped_dict[group_key]["article"] = article
                else:
                    grouped_dict[group_key] = {
                        "article": article,
                        "name": name_raw,
                        "qty": qty,
                        "unit": unit,
                        "poles": poles,
                        "current_a": current_a
                    }

        standardized_items = list(grouped_dict.values())
        logger.info(f"[Vision] Successfully parsed {len(standardized_items)} items using OpenRouter (grouped backend-side).")

        # Store to cache
        if standardized_items:
            _memory_cache[file_hash] = standardized_items
            save_cache()

        return standardized_items

    except Exception as e:
        logger.error(f"[Vision] OpenRouter Vision API parsing failed: {e}", exc_info=True)
        # Log error and return empty list to not break the pipeline
        return []
