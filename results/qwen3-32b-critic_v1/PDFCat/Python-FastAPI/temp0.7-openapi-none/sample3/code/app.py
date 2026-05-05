import subprocess
from fastapi import FastAPI, File, UploadFile, Response, HTTPException
from typing import List
import tempfile
import os
import uuid
import shutil

if not shutil.which("pdfunite"):
    raise SystemExit("pdfunite command not found. Please install poppler-utils.")

MAX_FILES = 10
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_TOTAL_SIZE = 100 * 1024 * 1024  # 100MB

app = FastAPI()

@app.post("/concatenate")
async def concatenate_pdfs(files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")
    
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Too many files. Maximum allowed is {MAX_FILES}.")

    try:
        with tempfile.TemporaryDirectory() as tmpdirname:
            file_paths = []
            total_size = 0
            for file in files:
                if file.content_type != 'application/pdf':
                    raise HTTPException(status_code=400, detail=f"File {file.filename} is not a PDF. Content type: {file.content_type}")
                
                contents = await file.read()
                if len(contents) == 0:
                    raise HTTPException(status_code=400, detail=f"File {file.filename} is empty.")
                
                if len(contents) > MAX_FILE_SIZE:
                    raise HTTPException(status_code=400, detail=f"File {file.filename} exceeds maximum size of {MAX_FILE_SIZE / 1024 / 1024}MB.")
                
                total_size += len(contents)
                if total_size > MAX_TOTAL_SIZE:
                    raise HTTPException(status_code=400, detail=f"Total size of files exceeds {MAX_TOTAL_SIZE / 1024 / 1024}MB.")
                
                if not contents.startswith(b'%PDF-'):
                    raise HTTPException(status_code=400, detail=f"File {file.filename} is not a valid PDF.")
                
                safe_filename = f"{uuid.uuid4().hex}.pdf"
                file_path = os.path.join(tmpdirname, safe_filename)
                with open(file_path, "wb") as buffer:
                    buffer.write(contents)
                file_paths.append(file_path)
            
            output_path = os.path.join(tmpdirname, "concatenated.pdf")
            command = ["pdfunite"] + file_paths + [output_path]
            
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                raise HTTPException(status_code=500, detail="An error occurred while processing the files.")
            
            with open(output_path, "rb") as f:
                pdf_data = f.read()
            
            return Response(content=pdf_data, media_type="application/pdf")
    
    except subprocess.CalledProcessError as e:
        print(f"Subprocess error: {e}")
        print(f"stdout: {e.stdout}")
        print(f"stderr: {e.stderr}")
        raise HTTPException(status_code=500, detail="An error occurred while processing the files.")
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="An error occurred while processing the files.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)