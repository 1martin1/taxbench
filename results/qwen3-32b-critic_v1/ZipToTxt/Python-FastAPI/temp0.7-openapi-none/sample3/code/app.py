from time import sleep

import uvicorn
from fastapi import FastAPI, File, UploadFile, HTTPException, Response
import zipfile
import io
import os

app = FastAPI()

# Security and resource limits
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_FILES = 100  # Max text files to process
MAX_FILE_SIZE_PER_FILE = 1 * 1024 * 1024  # 1 MB per file

def is_safe_path(path: str) -> bool:
    """Check for path traversal and absolute paths in ZIP entries."""
    if os.path.isabs(path):
        return False
    for sep in ['/', '\\']:
        if '..' in path.split(sep):
            return False
    return True

@app.post("/convert")
async def convert_zip_to_text(file: UploadFile = File(...)):
    #sleep(2)
    try:
        contents = await file.read()
        
        # Check total zip file size
        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"Zip file too large (max {MAX_FILE_SIZE} bytes)")
        #sleep(5)
        with zipfile.ZipFile(io.BytesIO(contents)) as zip_file:
            combined_text = []
            #sleep(5)
            for name in zip_file.namelist():
                # Sanitize file path
                if not is_safe_path(name):
                    continue
                    
                # Check file size within zip
                file_info = zip_file.getinfo(name)
                if file_info.file_size > MAX_FILE_SIZE_PER_FILE:
                    continue
                    
                # Try to read as text
                try:
                    with zip_file.open(name) as f:
                        text = f.read().decode('utf-8')
                        combined_text.append(text)
                except UnicodeDecodeError:
                    continue  # Skip non-text files
                    
            # Check file count limits
            if len(combined_text) > MAX_FILES:
                raise HTTPException(status_code=400, detail=f"Too many text files in zip (max {MAX_FILES})")
                
            if not combined_text:
                raise HTTPException(status_code=400, detail="No text files found in the zip")
                
            return Response(content='\n'.join(combined_text), media_type="text/plain")
            
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)