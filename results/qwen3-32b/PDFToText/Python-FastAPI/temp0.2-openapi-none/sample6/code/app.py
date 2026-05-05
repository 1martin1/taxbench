from fastapi import FastAPI, UploadFile, File, Response, JSONResponse, Request
from fastapi.exceptions import RequestValidationError
import subprocess
import tempfile
import os

app = FastAPI()

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": "Bad Request - Invalid input or missing file"},
    )

@app.post("/convert")
async def convert_pdf_to_text(file: UploadFile = File(...)):
    if not file.filename.lower().endswith('.pdf'):
        return JSONResponse(status_code=400, content={"error": "Invalid file type. Please upload a PDF file."})
    
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
            content = await file.read()
            tmp_file.write(content)
            tmp_file_path = tmp_file.name
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": "Failed to process uploaded file."})
    
    try:
        result = subprocess.run(
            ['pdftotext', tmp_file_path, '-'],
            capture_output=True,
            text=True,
            check=True
        )
        extracted_text = result.stdout
    except subprocess.CalledProcessError as e:
        return JSONResponse(status_code=500, content={"error": "PDF conversion failed."})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": "An unexpected error occurred during conversion."})
    finally:
        try:
            os.unlink(tmp_file_path)
        except Exception as e:
            pass
    
    return Response(content=extracted_text, media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)