import io
import logging
from PIL import Image
import pdfplumber
from pdf2image import convert_from_bytes
import pytesseract

logger = logging.getLogger("pdf_parser")

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    Extracts text from PDF bytes. Uses pdfplumber first.
    If the extracted text is empty or too short, falls back to OCR via pytesseract.
    """
    extracted_text = ""

    # 1. Try extracting text with pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_text = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)
            extracted_text = "\n".join(pages_text).strip()
    except Exception as e:
        logger.warning(f"pdfplumber extraction failed: {e}")

    # 2. Fallback to OCR using pdf2image and pytesseract if text is empty/too short
    if len(extracted_text) < 20:
        logger.info("PDF text content is too short or empty. Falling back to OCR...")
        try:
            # Convert PDF bytes to PIL images
            images = convert_from_bytes(pdf_bytes)
            ocr_pages = []
            for img in images:
                # Perform OCR with Russian and English support
                text = pytesseract.image_to_string(img, lang="rus+eng")
                if text:
                    ocr_pages.append(text)

            ocr_text = "\n".join(ocr_pages).strip()
            if ocr_text:
                extracted_text = ocr_text
        except Exception as e:
            logger.error(f"OCR fallback failed: {e}")
            # If OCR completely fails or tools aren't installed/configured,
            # we keep whatever pdfplumber extracted or return a descriptive fallback string.
            if not extracted_text:
                extracted_text = "Не удалось извлечь текст из PDF. Возможно, файл содержит только отсканированные изображения и OCR библиотека/зависимости не настроены."

    return extracted_text
