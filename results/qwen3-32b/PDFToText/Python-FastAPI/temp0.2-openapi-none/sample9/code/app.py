from fastapi import FastAPI, UploadFile, HTTPException, Response, Request, JSONResponse
from fastapi.exceptions import RequestValidationError
import subprocess
import tempfile
import os

app = FastAPI()

@app.post("/convert")
async def convert_pdf_to_text(file: UploadFile):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Invalid file type. Must be a PDF.")
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must have a .pdf extension.")
    
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            content = await file.read()
            tmp_file.write(content)
            tmp_path = tmp_file.name
        
        command = ["pdftotext", tmp_path, "-"]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
        
        if result.returncode != 0:
            os.remove(tmp_path)
            raise HTTPException(status_code=500, detail="PDF conversion failed.")
        
        os.remove(tmp_path)
        return Response(content=result.stdout, media_type="text/plain")
    
    except Exception as e:
        if 'tmp_path' in locals():
            try:
                os.remove(tmp_path)
            except:
                pass
        raise HTTPException(status_code=500, detail="Internal Server Error - Conversion failed") from e

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": "Bad Request - Invalid input or missing file"},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)