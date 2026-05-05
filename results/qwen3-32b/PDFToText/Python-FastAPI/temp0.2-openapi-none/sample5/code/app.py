import uuid
import os
import subprocess
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import Response
from fastapi.exceptions import RequestValidationError

app = FastAPI()

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return Response("Bad Request - Invalid input or missing file", status_code=400, media_type="text/plain")

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return Response(exc.detail, status_code=exc.status_code, media_type="text/plain")

@app.post("/convert")
async def convert_pdf(file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Invalid file type. Please upload a PDF file.")
    
    temp_pdf_path = f"/tmp/uploaded_pdf_{uuid.uuid4()}.pdf"
    try:
        content = await file.read()
        if not content.startswith(b'%PDF-'):
            raise HTTPException(status_code=400, detail="Invalid PDF file content.")
        with open(temp_pdf_path, "wb") as buffer:
            buffer.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to save uploaded file.")
    
    try:
        result = subprocess.run(
            ['pdftotext', temp_pdf_path, '-'],
            capture_output=True,
            text=True,
            check=True
        )
        return Response(content=result.stdout, media_type="text/plain")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="PDF conversion tool not available.")
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail="PDF conversion to text failed.")
    finally:
        if os.path.exists(temp_pdf_path):
            try:
                os.remove(temp_pdf_path)
            except Exception:
                pass  # Ignore cleanup errors

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)