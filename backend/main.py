from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import os

app = FastAPI(
    title="Service for generating commercial offers (КП) for switchboard equipment",
    description="Backend for extracting PDF text and managing commercial offers.",
    version="1.0.0"
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
    Accepts a PDF file and returns a mock JSON response with extracted data.
    """
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDF files are allowed.")

    # Read filename and mock text extraction for switchboard equipment
    filename = file.filename
    mock_extracted_text = (
        f"Extracted content from {filename}:\n"
        "1. Вводно-распределительное устройство (ВРУ-1) - 1 шт.\n"
        "2. Шкаф распределительный (ШР-11) - 2 шт.\n"
        "3. Автоматический выключатель 100А - 3 шт.\n"
        "4. Кабель силовой ВВГнг 3х2.5 - 100м."
    )

    # Return structured mockup JSON
    return JSONResponse(content={
        "status": "success",
        "filename": filename,
        "content_type": file.content_type,
        "extracted_text": mock_extracted_text,
        "items": [
            {"id": 1, "name": "Вводно-распределительное устройство (ВРУ-1)", "quantity": 1, "unit": "шт"},
            {"id": 2, "name": "Шкаф распределительный (ШР-11)", "quantity": 2, "unit": "шт"},
            {"id": 3, "name": "Автоматический выключатель 100А", "quantity": 3, "unit": "шт"},
            {"id": 4, "name": "Кабель силовой ВВГнг 3х2.5", "quantity": 100, "unit": "м"}
        ]
    })

# Mount frontend files to serve the static SPA
# In production/deployment, static files can be served by FastAPI or configured otherwise
frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend"))
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
