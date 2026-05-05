from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import Response
import subprocess
import tempfile
import os

app = FastAPI()

@app.post("/convert")
async def convert_pdf_to_text(file: UploadFile = File(...)):
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDFs are allowed.")
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = os.path.join(tmpdir, "input.pdf")
            txt_path = os.path.join(tmpdir, "output.txt")
            content = await file.read()
            with open(pdf_path, "wb") as buffer:
                buffer.write(content)
            subprocess.run(["pdftotext", pdf_path, txt_path], check=True)
            with open(txt_path, "r") as txt_file:
                text = txt_file.read()
        return Response(content=text, media_type="text/plain")
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail="PDF conversion failed")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)