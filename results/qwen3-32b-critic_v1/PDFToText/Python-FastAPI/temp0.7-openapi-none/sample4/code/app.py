from fastapi import FastAPI, File, UploadFile, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
import tempfile
import subprocess
import os
import asyncio

app = FastAPI()

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"error": "Bad Request - Invalid input or missing file"})

@app.post("/convert")
async def convert_pdf_to_text(file: UploadFile = File(...)):
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
    PDF_MAGIC_BYTES = b"%PDF-"
    
    content = await file.read()
    
    # Validate file size
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File size exceeds maximum allowed (10MB)")
    
    # Validate PDF content using magic bytes
    if not content.startswith(PDF_MAGIC_BYTES):
        raise HTTPException(status_code=400, detail="Invalid PDF file")
    
    # Write to temporary file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
        temp_pdf.write(content)
        temp_pdf_path = temp_pdf.name
    
    try:
        # Run pdftotext with timeout
        result = subprocess.run(
            ["pdftotext", temp_pdf_path, "-"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30  # 30 seconds timeout
        )
        return Response(content=result.stdout, media_type="text/plain")
    except subprocess.CalledProcessError as e:
        return JSONResponse(status_code=500, content={"error": "Internal Server Error - Conversion failed"})
    except subprocess.TimeoutExpired:
        return JSONResponse(status_code=500, content={"error": "Conversion process timed out"})
    finally:
        # Ensure temporary file is deleted
        try:
            os.unlink(temp_pdf_path)
        except Exception as e:
            pass  # Log error if needed, but don't fail the request

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)