import os
import subprocess
import tempfile
from fastapi import FastAPI, UploadFile, File, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

app = FastAPI()

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": "Invalid input or missing file"},
    )

@app.post("/convert")
async def convert_pdf_to_text(file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith('.pdf'):
        return JSONResponse(status_code=400, content={"error": "File is not a PDF"})
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_file_path = os.path.join(tmp_dir, file.filename)
        try:
            with open(tmp_file_path, "wb") as buffer:
                content = await file.read()
                buffer.write(content)
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": "Failed to save file"})
        
        try:
            result = subprocess.run(
                ['pdftotext', tmp_file_path, '-'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            return Response(content=result.stdout, media_type="text/plain")
        except subprocess.CalledProcessError as e:
            return JSONResponse(status_code=500, content={"error": "Conversion failed"})
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": "Internal server error"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)