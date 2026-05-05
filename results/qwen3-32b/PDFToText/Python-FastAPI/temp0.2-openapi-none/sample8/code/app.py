from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import Response
import subprocess
import tempfile
import shutil
import os

app = FastAPI()

@app.post("/convert")
async def convert_pdf_to_text(file: UploadFile = File(None)):
    if not file:
        raise HTTPException(status_code=400, detail="Missing file")
    
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDFs are allowed.")
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_pdf_path = os.path.join(tmpdir, "temp.pdf")
            with open(temp_pdf_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
            
            result = subprocess.run(
                ["pdftotext", temp_pdf_path, "-"],
                capture_output=True,
                text=True,
                check=True
            )
            return Response(content=result.stdout, media_type="text/plain")
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail="PDF conversion failed") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)