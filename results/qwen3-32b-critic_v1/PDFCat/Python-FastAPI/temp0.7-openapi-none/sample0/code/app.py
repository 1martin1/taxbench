from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import Response
import subprocess
import tempfile
import os
import asyncio

app = FastAPI()

MAX_FILES = 20
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
PDF_MAGIC_NUMBER = b'%PDF-'

def check_pdfunite_installed():
    try:
        subprocess.run(["pdfunite", "--version"], 
                      stdout=subprocess.DEVNULL, 
                      stderr=subprocess.DEVNULL,
                      check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise RuntimeError("pdfunite is not installed. Please install poppler-utils.")

@app.on_event("startup")
def startup_event():
    check_pdfunite_installed()

@app.post("/concatenate")
async def concatenate_pdfs(files: list[UploadFile] = File(..., max_length=MAX_FILE_SIZE)):
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_FILES} files allowed")
    
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_files = []
            for idx, file in enumerate(files):
                # Validate content type
                if file.content_type != "application/pdf":
                    raise HTTPException(status_code=400, 
                                      detail=f"File {file.filename} is not a PDF")
                
                file_path = os.path.join(temp_dir, f"file_{idx}.pdf")
                try:
                    # Write file in chunks and validate PDF magic number
                    with open(file_path, "wb") as f:
                        # Read first chunk to validate PDF magic number
                        first_chunk = await file.read(5)
                        if first_chunk != PDF_MAGIC_NUMBER:
                            raise HTTPException(status_code=400, 
                                              detail=f"File {file.filename} is not a valid PDF")
                        f.write(first_chunk)
                        
                        # Read and write remaining chunks
                        while True:
                            chunk = await file.read(1024 * 1024)  # 1MB chunks
                            if not chunk:
                                break
                            f.write(chunk)
                    input_files.append(file_path)
                except HTTPException:
                    # Clean up partially written files
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    raise
            
            output_path = os.path.join(temp_dir, "concatenated.pdf")
            command = ["pdfunite"] + input_files + [output_path]
            
            # Run with timeout and capture output
            subprocess.run(command, check=True, timeout=30)
            
            with open(output_path, "rb") as f:
                pdf_data = f.read()
                
        return Response(content=pdf_data, media_type="application/pdf")
        
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, 
                          detail="PDF concatenation failed") from e
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=500, 
                          detail="PDF concatenation timed out") from e
    except Exception as e:
        raise HTTPException(status_code=500, 
                          detail="An error occurred while processing the files") from e

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)