from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, StreamingResponse
import os
import io
import json
from typing import Dict, Any, List, Optional

from backend.pdf_parser import extract_text_from_pdf, parse_pdf_combined_to_bom
from backend.bom_parser import analyze_equipment
from backend.price_parser import parse_price_list, build_and_save_index
from backend.kp_generator import generate_preliminary_kp, export_kp_to_excel

app = FastAPI(
    title="Service for generating commercial offers (КП) for switchboard equipment",
    description="Backend for extracting PDF text and managing commercial offers directly against Excel price lists.",
    version="2.0.0"
)

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def convert_boards_to_flat_equipment(boards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Converts structured board groups into a flat equipment list format for backwards compatibility."""
    flat_items = []
    item_id = 1
    for board in boards:
        for item in board.get("items", []):
            display_name = item["name"]
            if item.get("article") and item["article"] not in display_name:
                display_name = f"{display_name} ({item['article']})"
            flat_items.append({
                "id": item_id,
                "name": display_name,
                "qty": item["qty"],
                "quantity": item["qty"],
                "unit": item.get("unit", "шт")
            })
            item_id += 1
    return flat_items

@app.get("/api/health")
async def health_check():
    """Health check endpoint for Railway deployment and monitoring."""
    return {"status": "ok", "service": "BEP"}

@app.get("/api/active-pricelist")
async def get_active_pricelist():
    """Returns the original filename of the currently active/saved pricelist on disk."""
    metadata_path = "data/pricelists/metadata.json"
    active_path = "data/pricelists/active_pricelist.xlsx"
    if os.path.exists(active_path) and os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            return {"status": "success", "filename": metadata.get("filename", "active_pricelist.xlsx")}
        except Exception as e:
            return {"status": "success", "filename": "active_pricelist.xlsx"}
    return {"status": "empty", "filename": None}

@app.post("/api/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)):
    """
    Accepts a PDF file, extracts its text content, and detects switchboard equipment positions.
    Utilizes Gemini Vision API combined with classic text/regex BOM extraction.
    """
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDF files are allowed.")

    try:
        # Read uploaded PDF bytes
        pdf_bytes = await file.read()

        # Extract text from PDF
        extracted_text = extract_text_from_pdf(pdf_bytes)

        # Parse equipment BOM positions using new combined Vision + classic parser
        boards = await parse_pdf_combined_to_bom(pdf_bytes)
        equipment = convert_boards_to_flat_equipment(boards)

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

@app.post("/api/generate-kp")
async def generate_kp(
    specification: UploadFile = File(...),
    pricelists: List[UploadFile] = File(None)
):
    """
    Real business-workflow endpoint:
    Accepts PDF technical document specification AND one or more optional Excel price list sheets.
    If pricelists are provided, saves them persistently on disk and builds/saves the indexing knowledge base.
    If pricelists are not provided, uses the currently active/saved pricelist and its index on disk.
    Parses PDF, extracts BOM, compiles lookup prices map from pricelists, matches,
    and returns a structured Commercial Proposal JSON.
    """
    if not specification.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Specification file must be a PDF document.")

    if pricelists:
        for p_list in pricelists:
            if not p_list.filename.lower().endswith(('.xlsx', '.xls')):
                raise HTTPException(status_code=400, detail="Price lists must be Excel spreadsheets (.xlsx, .xls).")

    try:
        # Create directory for price list storage
        os.makedirs("data/pricelists", exist_ok=True)

        # 1. Parse PDF specification text and OpenRouter Vision combined
        pdf_bytes = await specification.read()
        extracted_text = extract_text_from_pdf(pdf_bytes)
        boards = await parse_pdf_combined_to_bom(pdf_bytes)

        # 2. Parse, merge and index uploaded price lists
        price_map = {}
        index_map = {}
        if pricelists:
            # Overwrite active price lists and structured indices on disk with newly uploaded files
            for p_idx, p_list in enumerate(pricelists):
                price_bytes = await p_list.read()

                # Build index map for the price list
                file_index = build_and_save_index(price_bytes)
                # Merge indices
                for k, v in file_index.items():
                    if k not in index_map:
                        index_map[k] = []
                    index_map[k].extend(v)

                price_map = parse_price_list(price_bytes, price_map)

                # Save the first price list on disk as our primary/active pricelist
                if p_idx == 0:
                    active_path = "data/pricelists/active_pricelist.xlsx"
                    with open(active_path, "wb") as f:
                        f.write(price_bytes)
                    # Save metadata
                    metadata = {"filename": p_list.filename}
                    with open("data/pricelists/metadata.json", "w", encoding="utf-8") as mf:
                        json.dump(metadata, mf, ensure_ascii=False, indent=2)

            # Save the compiled active index on disk
            index_path = "data/pricelists/active_index.json"
            with open(index_path, "w", encoding="utf-8") as index_file:
                json.dump(index_map, index_file, ensure_ascii=False, indent=2)
        else:
            # No pricelists uploaded, load from persistent storage
            active_path = "data/pricelists/active_pricelist.xlsx"
            index_path = "data/pricelists/active_index.json"
            if os.path.exists(active_path) and os.path.exists(index_path):
                with open(active_path, "rb") as f:
                    price_bytes = f.read()
                price_map = parse_price_list(price_bytes, price_map)

                # Load existing persistent index map
                with open(index_path, "r", encoding="utf-8") as index_file:
                    index_map = json.load(index_file)
            else:
                raise HTTPException(status_code=400, detail="Прайс-лист не найден. Пожалуйста, загрузите хотя бы один прайс-лист (.xlsx).")

        # 3. Generate the preliminary commercial proposal
        kp_data = generate_preliminary_kp(boards, price_map, index_map)

        return JSONResponse(content={
            "status": "success",
            "specification_file": specification.filename,
            "pricelist_count": len(pricelists) if pricelists else 1,
            "extracted_text": extracted_text,
            "kp": kp_data
        })
    except Exception as e:
        logger_name = "main"
        import logging
        logging.getLogger(logger_name).error(f"Failed to generate KP: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate KP: {str(e)}")

@app.post("/api/export-kp")
async def export_kp(kp_data: Dict[str, Any] = Body(...)):
    """
    Accepts KP JSON representation and exports it to a highly polished and styled Excel file.
    """
    try:
        excel_bytes = export_kp_to_excel(kp_data)
        return StreamingResponse(
            io.BytesIO(excel_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=preliminary_commercial_proposal.xlsx"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to export KP: {str(e)}")

# Mount frontend files to serve the static SPA
# In production/deployment, static files can be served by FastAPI or configured otherwise
frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../frontend"))
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
