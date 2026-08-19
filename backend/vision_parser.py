import os
import json
import hashlib
import logging
import asyncio
import base64
import io
import re
from typing import List, Dict, Any, Optional
import tempfile
import pymupdf
from PIL import Image, ImageEnhance
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
    """
    Applies upscaling (3x) with LANCZOS, enhances sharpness (2.0),
    saves to a temp file, reads it, and encodes to base64 PNG string.
    """
    try:
        # Get original dimensions
        width, height = img.size
        new_width = width * 3
        new_height = height * 3

        # Use Image.Resampling.LANCZOS or Image.LANCZOS based on Pillow version
        try:
            resample_filter = Image.Resampling.LANCZOS
        except AttributeError:
            resample_filter = Image.LANCZOS

        # Resize/Upscale 3x
        resized_img = img.resize((new_width, new_height), resample_filter)

        # Enhance sharpness (coefficient ~2.0)
        enhancer = ImageEnhance.Sharpness(resized_img)
        enhanced_img = enhancer.enhance(2.0)

        # Save to a temporary file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
            temp_path = tmp_file.name

        try:
            enhanced_img.save(temp_path, format="PNG")
            with open(temp_path, "rb") as f:
                img_bytes = f.read()
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

        return base64.b64encode(img_bytes).decode("utf-8")
    except Exception as e:
        logger.error(f"[Vision] Error processing PIL image: {e}")
        # Fallback to simple bytes encoding in case of failure
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

async def parse_equipment_from_pdf(pdf_path: str, custom_prompt: Optional[str] = None, selected_pages: Optional[List[int]] = None) -> List[Dict[str, Any]]:
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

        # Convert PDF pages to PIL images via PyMuPDF (fast & reliable) or pdf2image fallback
        logger.info(f"[Vision] Converting PDF {pdf_path} to images (selected_pages={selected_pages})...")
        images = []
        try:
            doc = pymupdf.open(pdf_path)
            target_pages = selected_pages if selected_pages is not None else [0, 1]
            for p_idx in target_pages:
                if 0 <= p_idx < len(doc):
                    page = doc[p_idx]
                    pix = page.get_pixmap(dpi=150)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    images.append(img)
        except Exception as p_err:
            logger.warning(f"[Vision] PyMuPDF page rendering failed: {p_err}. Falling back to pdf2image.")
            first_p = (selected_pages[0] + 1) if (selected_pages and len(selected_pages) > 0) else 1
            last_p = (selected_pages[-1] + 1) if (selected_pages and len(selected_pages) > 0) else 2
            images = await asyncio.to_thread(convert_from_path, pdf_path, dpi=150, first_page=first_p, last_page=last_p)

        if not images:
            logger.warning(f"[Vision] No pages extracted from PDF: {pdf_path}")
            return []

        images_to_send = images

        # Prepare strict prompt as requested by the user
        if custom_prompt:
            prompt = custom_prompt
            logger.info(f"[Vision] Using custom prompt: {custom_prompt[:150]}...")
        else:
            prompt = """Ты инженер-сметчик по электрощитовому оборудованию CHINT.
На схеме найди и распознай ВСЕ электрические аппараты и силовые устройства.
Распознай следующие типы приборов:
1. Автоматические выключатели (обозначение QF) — тип "автомат"
2. Рубильники / Выключатели нагрузки (обозначение QS) — тип "рубильник"
3. УЗО (Выключатели дифференциального тока, обозначение QD) — тип "УЗО"
4. Дифференциальные автоматы (АВДТ, обозначение QF/QFD) — тип "дифавтомат"
5. Контакторы (обозначение KM) — тип "контактор"
6. Преобразователи частоты (частотники, обозначение U) — тип "преобразователь"

Для каждого найденного прибора заполни поля:
- mark: позиционное обозначение на схеме (QF1, QS3, KM2 и т.д.)
- series: серия прибора CHINT (например: NM8N, NB2, NH4, NL1, NB1L, NC8, NVF7), если видна или может быть определена
- nominal: номинальный ток или мощность (например: 125A, 63А, 16А, 7.5кВт)
- poles: количество полюсов (например: 1P, 2P, 3P, 4P)
- type: один из типов на русском языке ("автомат", "рубильник", "УЗО", "дифавтомат", "контактор", "преобразователь")
- qty: количество приборов этой группы

Группируй абсолютно идентичные приборы (с одинаковыми nominal, poles и type) — складывай их количество в поле qty.
Игнорируй: кабели, шины, надписи "Сортер", "Резерв", размеры в мм, кадастровые адреса, штампы листов.
Ответ верни СТРОГО как один валидный JSON-массив приборов, без лишнего текста вокруг:
[{"mark":"QF1","series":"NM8N","nominal":"125A","poles":"3P","type":"автомат","qty":2}]"""
            logger.info("[Vision] Using default fallback prompt.")

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
                temperature=0.1,
                response_format={"type": "json_object"}
            )
        except Exception as e:
            # Check for 404 Not Found or 429 Too Many Requests to invoke fallback
            is_fallback_trigger = False
            if hasattr(e, "status_code") and e.status_code in [404, 429]:
                is_fallback_trigger = True
            elif any(indicator in str(e).upper() or indicator in str(e) for indicator in ["404", "429", "RESOURCE_EXHAUSTED", "RATE_LIMIT", "LIMIT_EXCEEDED", "NOT_FOUND"]):
                is_fallback_trigger = True

            if is_fallback_trigger:
                logger.warning(f"[Vision] 429/404 on model {primary_model}: {e}. Trying fallback model {fallback_model}...")
                try:
                    response = await asyncio.to_thread(
                        client.chat.completions.create,
                        model=fallback_model,
                        messages=[
                            {
                                "role": "user",
                                "content": content_payload
                            }
                        ],
                        temperature=0.1,
                        response_format={"type": "json_object"}
                    )
                except Exception as fallback_err:
                    logger.warning(f"[Vision] 429/empty response on fallback model {fallback_model}: {fallback_err}. Invoking text-fallback...")
                    return []
            else:
                logger.warning(f"[Vision] Vision API call failed: {e}. Invoking text-fallback...")
                return []

        if not response or not response.choices or not response.choices[0].message.content:
            logger.warning("[Vision] OpenRouter API returned an empty response.")
            return []

        # Parse output JSON using re.search for [.*] as requested
        raw_text = response.choices[0].message.content
        logger.debug(f"Raw response from OpenRouter: {raw_text}")

        parsed_json = []
        try:
            array_match = re.search(r'\[.*\]', raw_text, re.DOTALL)
            if array_match:
                cleaned_text = array_match.group(0)
            else:
                cleaned_text = clean_json_response(raw_text)
            parsed_json = json.loads(cleaned_text)
        except Exception as parse_err:
            logger.warning(f"[Vision] Regex/JSON array parse failed: {parse_err}. Trying simple load.")
            try:
                cleaned_text = clean_json_response(raw_text)
                parsed_json = json.loads(cleaned_text)
            except Exception as e2:
                logger.error(f"[Vision] All JSON parsing attempts failed: {e2}")
                parsed_json = []

        # Resolve parsed_json into list of items
        items_list = []
        if isinstance(parsed_json, dict):
            if "items" in parsed_json:
                items_list = parsed_json["items"]
            else:
                items_list = [parsed_json]
        elif isinstance(parsed_json, list):
            items_list = parsed_json

        # Ensure all items match the new format with mark, nominal, type, poles, etc.
        standardized_items = []
        for item in items_list:
            if not isinstance(item, dict):
                continue
            standardized_items.append({
                "mark": item.get("mark"),
                "series": item.get("series"),
                "nominal": item.get("nominal"),
                "poles": item.get("poles"),
                "type": item.get("type", "автомат"),
                "qty": item.get("qty", 1)
            })

        logger.info(f"[Vision] Successfully parsed {len(standardized_items)} items using OpenRouter.")

        # Store to cache
        if standardized_items:
            _memory_cache[file_hash] = standardized_items
            save_cache()

        return standardized_items

    except Exception as e:
        logger.error(f"[Vision] OpenRouter Vision API parsing failed: {e}", exc_info=True)
        # Purge stale cache for this hash if an error occurred
        if 'file_hash' in locals() and file_hash in _memory_cache:
            del _memory_cache[file_hash]
            save_cache()
        return []
