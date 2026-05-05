import os
import subprocess
import tempfile
from fastapi import FastAPI, File, UploadFile, HTTPException, Response
from typing import List

app = FastAPI()

MAX_FILES = 20
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

@app.post("/concatenate")
async def concatenate_pdfs(files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")
    
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_paths = []
            for i, file in enumerate(files):
                content = await file.read()
                if len(content) > MAX_FILE_SIZE:
                    raise HTTPException(status_code=400, detail="Invalid input or missing files.")
                if not content.startswith(b"%PDF-"):
                    raise HTTPException(status_code=400, detail="Invalid input or missing files.")
                
                safe_filename = f"file_{i}.pdf"
                file_path = os.path.join(temp_dir, safe_filename)
                with open(file_path, "wb") as f:
                    f.write(content)
                file_paths.append(file_path)
            
            output_path = os.path.join(temp_dir, "concatenated.pdf")
            try:
                subprocess.check_call(
                    ["pdfunite"] + file_paths + [output_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                raise HTTPException(status_code=500, detail="An error occurred while processing the files.") from e
            
            with open(output_path, "rb") as f:
                pdf_bytes = f.read()
        
        return Response(content=pdf_bytes, media_type="application/pdf")
    
    except Exception as e:
        raise HTTPException(status_code=500, detail="An error occurred while processing the files.") from e

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)