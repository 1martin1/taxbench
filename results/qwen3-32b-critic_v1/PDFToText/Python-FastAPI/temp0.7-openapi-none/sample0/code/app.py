from fastapi import FastAPI, File, UploadFile, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import subprocess
import tempfile
import os

app = FastAPI()

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": "Bad Request - Invalid input or missing file"}
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )

@app.post("/convert")
async def convert_pdf(file: UploadFile = File(..., max_size=10 * 1024 * 1024)):  # 10MB limit
    # Validate file type
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Must be a PDF."
        )
    
    # Read and validate content
    content = await file.read()
    if not content.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=400,
            detail="Invalid PDF content."
        )
    
    temp_pdf_path = None
    try:
        # Create temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
            temp_pdf.write(content)
            temp_pdf_path = temp_pdf.name
        
        # Execute conversion
        result = subprocess.run(
            ["pdftotext", temp_pdf_path, "-"],
            capture_output=True,
            text=True,
            check=True
        )
        
        return JSONResponse(
            content=result.stdout,
            media_type="text/plain"
        )
    
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": "Internal Server Error - Conversion failed"}
        )
    
    finally:
        # Ensure cleanup
        if temp_pdf_path and os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)