import tempfile
import subprocess
import os
from fastapi import FastAPI, UploadFile, File, JSONResponse, Response, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
import asyncio

app = FastAPI()

# Security and resource limits
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
PDF_MAGIC_BYTES = b'%PDF-'
CONVERSION_TIMEOUT = 30  # seconds

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": "Bad Request - Invalid input or missing file"}
    )

@app.post("/convert")
async def convert_pdf_to_text(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        return JSONResponse(status_code=400, content={"error": "Invalid file type. Please upload a PDF file."})
    
    try:
        content = await file.read()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": "Failed to read uploaded file"})

    if len(content) > MAX_FILE_SIZE:
        return JSONResponse(status_code=400, content={"error": "File size exceeds the maximum allowed size of 10MB"})
    
    if not content.startswith(PDF_MAGIC_BYTES):
        return JSONResponse(status_code=400, content={"error": "Invalid PDF file format"})
    
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_file.write(content)
            temp_file_path = temp_file.name
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": "Failed to create temporary file"})
    
    try:
        result = subprocess.run(
            ['pdftotext', temp_file_path, '-'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,
            timeout=CONVERSION_TIMEOUT
        )
        return Response(content=result.stdout, media_type="text/plain")
    except subprocess.CalledProcessError as e:
        return JSONResponse(status_code=500, content={"error": f"PDF conversion failed: {e.stderr}"})
    except subprocess.TimeoutExpired as e:
        return JSONResponse(status_code=500, content={"error": "PDF conversion timed out after 30 seconds"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": "PDF conversion failed"})
    finally:
        try:
            os.remove(temp_file_path)
        except Exception:
            pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)