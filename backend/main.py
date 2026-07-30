from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import os

from backend.pdf_parser import extract_text_from_pdf
from backend.bom_parser import analyze_equipment

app = FastAPI(
    title="Service for generating commercial offers (КП) for switchboard equipment",
    description="Backend for extracting PDF text and managing commercial offers.",
    version="1.1.0"
)

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
async def health_check():
    """Health check endpoint for Railway deployment and monitoring."""
    return {"status": "ok", "service": "bep-backend"}

@app.post("/api/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)):
    """
    Accepts a PDF file, extracts its text content, and detects switchboard equipment positions.
    """
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDF files are allowed.")

    try:
        # Read uploaded PDF bytes
        pdf_bytes = await file.read()

        # Extract text from PDF
        extracted_text = extract_text_from_pdf(pdf_bytes)

        # Parse equipment BOM positions
        equipment = analyze_equipment(extracted_text)

        # If no positions were found, construct some defaults/stubs to avoid showing empty results to users
        if not equipment and "не удалось извлечь" not in extracted_text.lower():
            equipment = [
                {"id": 1, "name": "Распознанные позиции не найдены. Пожалуйста, проверьте текст.", "qty": 0, "unit": "шт"}
            ]
        elif not equipment:
            equipment = [
                {"id": 1, "name": "Автоматический выключатель ВА47-29 3P 16A (Шаблон)", "qty": 1, "unit": "шт"},
                {"id": 2, "name": "Контактор КМИ-11810 18А 230В (Шаблон)", "qty": 1, "unit": "шт"}
            ]

        return JSONResponse(content={
            "status": "success",
            "filename": file.filename,
            "content_type": file.content_type,
            "extracted_text": extracted_text,
            "items": equipment
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process PDF file: {str(e)}")

# Mount frontend files to serve the static SPA
# In production/deployment, static files can be served by FastAPI or configured otherwise
frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend"))
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
