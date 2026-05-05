import subprocess
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import Response, JSONResponse
import tempfile
import os

app = FastAPI()

@app.post("/convert")
async def convert_pdf_to_text(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        return JSONResponse(status_code=400, content={"error": "File must be a PDF"})
    
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            content = await file.read()
            tmp_file.write(content)
            tmp_file_path = tmp_file.name
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": "Failed to save uploaded file"})
    
    try:
        result = subprocess.run(
            ["pdftotext", tmp_file_path, "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        os.remove(tmp_file_path)
        error_message = e.stderr or "PDF conversion failed"
        return JSONResponse(status_code=500, content={"error": error_message})
    
    os.remove(tmp_file_path)
    return Response(content=result.stdout, media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)