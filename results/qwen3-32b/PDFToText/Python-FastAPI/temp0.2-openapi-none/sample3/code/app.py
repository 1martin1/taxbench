from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse
import tempfile
import subprocess
import os
import uvicorn

app = FastAPI()

@app.post("/convert")
async def convert_pdf_to_text(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")
    
    tmp_file_path = None
    try:
        content = await file.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
            tmp_file.write(content)
            tmp_file_path = tmp_file.name
        
        result = subprocess.run(
            ['pdftotext', tmp_file_path, '-'],
            capture_output=True,
            text=True,
            check=True
        )
        return PlainTextResponse(result.stdout)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail="Conversion failed") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal Server Error") from e
    finally:
        if tmp_file_path and os.path.exists(tmp_file_path):
            os.remove(tmp_file_path)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return PlainTextResponse("Bad Request - Invalid input or missing file", status_code=400)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)