import os
import pytest
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def test_health_check():
    """Test that the health check returns 200 and correct status."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "bep-backend"}

def test_upload_non_pdf():
    """Test that uploading a non-PDF file returns a 400 error."""
    # Create a mock text file
    files = {"file": ("test.txt", b"some text content", "text/plain")}
    response = client.post("/api/upload-pdf", files=files)
    assert response.status_code == 400
    assert "Invalid file type" in response.json()["detail"]

def test_upload_pdf_success():
    """Test that uploading a PDF successfully returns mock extracted data."""
    # Create a mock PDF file payload
    files = {"file": ("spec.pdf", b"%PDF-1.4 mock content", "application/pdf")}
    response = client.post("/api/upload-pdf", files=files)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["filename"] == "spec.pdf"
    assert "extracted_text" in data
    assert "items" in data
    assert len(data["items"]) == 4
