import os
import subprocess
import tempfile
from fastapi import FastAPI, UploadFile, File, List, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse, Response
from starlette.config import Config

app = FastAPI()

# Security/Validation Constants
MAX_FILES = 20
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

@app.on_event("startup")
async def check_pdfunite_installed():
    try:
        subprocess.run(
            ["pdfunite", "--version"],
            capture_output=True,
            check=True,
            timeout=5
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        raise RuntimeError("pdfunite is not installed or not accessible.")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": "Invalid input or missing files."}
    )

@app.post("/concatenate")
async def concatenate_pdfs(files: List[UploadFile] = File(...)):
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_FILES} files allowed")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        file_paths = []
        for i, file in enumerate(files):
            if file.content_type != "application/pdf":
                raise HTTPException(status_code=400, detail=f"File {file.filename} is not a PDF")
            
            try:
                contents = await file.read()
            except Exception as e:
                raise HTTPException(status_code=500, detail="Error reading file content")
            
            if len(contents) == 0:
                raise HTTPException(status_code=400, detail=f"File {file.filename} is empty")
            
            if len(contents) > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=f"File {file.filename} exceeds {MAX_FILE_SIZE/1_000_000}MB size limit"
                )
            
            if not contents.startswith(b"%PDF-"):
                raise HTTPException(status_code=400, detail=f"File {file.filename} is not a valid PDF")
            
            file_path = os.path.join(temp_dir, f"file_{i}.pdf")
            try:
                with open(file_path, "wb") as f:
                    f.write(contents)
            except Exception as e:
                raise HTTPException(status_code=500, detail="Error writing file to disk")
            
            file_paths.append(file_path)
        
        output_path = os.path.join(temp_dir, "concatenated.pdf")
        command = ["pdfunite"] + file_paths + [output_path]
        
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=30
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail="PDF processing failed")
        
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail="PDF processing failed")
        
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise HTTPException(status_code=500, detail="PDF processing failed")
        
        try:
            with open(output_path, "rb") as f:
                pdf_bytes = f.read()
        except Exception as e:
            raise HTTPException(status_code=500, detail="Error reading output file")
        
        return Response(content=pdf_bytes, media_type="application/pdf")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)