from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import Response
import tempfile
import subprocess
import os

app = FastAPI()

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
PDF_HEADER = b"%PDF-"

@app.post("/convert")
async def convert_pdf_to_text(file: UploadFile = File(...)):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Invalid file type. Must be a PDF.")
    
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "input.pdf")
            
            # Validate file content and stream in chunks
            with open(file_path, "wb") as buffer:
                # Read first 5 bytes to check PDF header
                first_chunk = await file.read(5)
                if not first_chunk.startswith(PDF_HEADER):
                    raise HTTPException(status_code=400, detail="Invalid PDF file content.")
                buffer.write(first_chunk)
                total_size = len(first_chunk)
                
                if total_size > MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail="File too large.")
                
                # Read remaining data in 8KB chunks
                while True:
                    chunk = await file.read(8192)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > MAX_FILE_SIZE:
                        raise HTTPException(status_code=413, detail="File too large.")
                    buffer.write(chunk)
            
            # Execute conversion with timeout
            result = subprocess.run(
                ["pdftotext", file_path, "-"],
                capture_output=True,
                text=True,
                check=True,
                timeout=30
            )
            return Response(content=result.stdout, media_type="text/plain")
            
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail="Conversion failed")
    except HTTPException:
        raise  # Re-raise HTTP exceptions directly
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)