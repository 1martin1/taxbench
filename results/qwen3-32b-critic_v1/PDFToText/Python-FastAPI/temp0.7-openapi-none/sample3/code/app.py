import os
import subprocess
import tempfile
from fastapi import FastAPI, File, UploadFile, HTTPException, Request, RequestEntityTooLarge
from fastapi.exceptions import RequestValidationError, RequestEntityTooLarge
from fastapi.responses import JSONResponse, Response

app = FastAPI()

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
CONVERSION_TIMEOUT = 30  # seconds

@app.post("/convert")
async def convert_pdf_to_text(file: UploadFile = File(..., max_length=MAX_FILE_SIZE)):
    # Validate file content type and extension
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Invalid input or missing file")
    if file.content_type != 'application/pdf':
        raise HTTPException(status_code=400, detail="Invalid input or missing file")

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_pdf:
            content = await file.read()
            tmp_pdf.write(content)
            tmp_pdf_path = tmp_pdf.name
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to save uploaded file") from e

    try:
        result = subprocess.run(
            ['pdftotext', tmp_pdf_path, '-'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=CONVERSION_TIMEOUT
        )
        text = result.stdout.decode('utf-8')
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode('utf-8', errors='ignore') or "Unknown error occurred"
        raise HTTPException(status_code=500, detail=f"Conversion failed: {error_msg}")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail=f"Conversion failed: Process exceeded {CONVERSION_TIMEOUT} second timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Conversion failed: {str(e)}")
    finally:
        if os.path.exists(tmp_pdf_path):
            os.remove(tmp_pdf_path)

    return Response(content=text, media_type="text/plain")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": "Bad Request - Invalid input or missing file"},
    )

@app.exception_handler(RequestEntityTooLarge)
async def too_large_handler(request: Request, exc: RequestEntityTooLarge):
    return JSONResponse(
        status_code=400,
        content={"error": "Bad Request - Invalid input or missing file"},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)