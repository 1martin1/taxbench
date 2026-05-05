import shutil
import tempfile
import os
import uuid
from fastapi import FastAPI, UploadFile, File, HTTPException, List
import subprocess
from fastapi.responses import Response

# Constants for input limits
MAX_FILES = 100
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_TOTAL_SIZE = 1 * 1024 * 1024 * 1024  # 1GB

# Check if pdfunite is available
if not shutil.which("pdfunite"):
    raise RuntimeError("pdfunite is not installed. Please install poppler-utils.")

app = FastAPI()

@app.post("/concatenate")
async def concatenate_pdfs(files: List[UploadFile] = File(..., max_length=MAX_FILE_SIZE)):
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Too many files. Maximum allowed: {MAX_FILES}.")
    
    total_size = sum(file.size for file in files)
    if total_size > MAX_TOTAL_SIZE:
        raise HTTPException(status_code=400, detail=f"Total file size exceeds the limit of {MAX_TOTAL_SIZE} bytes.")
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_paths = []
            for file in files:
                content = await file.read()
                # Validate that the file is a PDF by checking the magic number
                if not content.startswith(b'%PDF-'):
                    raise HTTPException(status_code=400, detail=f"File {file.filename} is not a valid PDF.")
                
                filename = f"{uuid.uuid4()}_{file.filename}"
                filepath = os.path.join(tmpdir, filename)
                with open(filepath, "wb") as f:
                    f.write(content)
                temp_paths.append(filepath)
            
            output_path = os.path.join(tmpdir, "concatenated.pdf")
            cmd = ["pdfunite"] + temp_paths + [output_path]
            subprocess.run(cmd, check=True, timeout=30)
            
            with open(output_path, "rb") as f:
                pdf_data = f.read()
            
            return Response(content=pdf_data, media_type="application/pdf")
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail="An error occurred while processing the files.")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="PDF processing timed out.")
    except Exception as e:
        raise HTTPException(status_code=500, detail="An error occurred while processing the files.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)