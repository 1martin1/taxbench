import zipfile
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import Response
import io
import uvicorn
from pathlib import PurePosixPath

app = FastAPI()

MAX_FILES = 100  # Security limit to prevent resource exhaustion

@app.post("/convert")
async def convert(file: UploadFile = File(...)):
    contents = await file.read()
    try:
        zip_file = zipfile.ZipFile(io.BytesIO(contents))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")
    
    # Prevent resource exhaustion with large archives
    if len(zip_file.namelist()) > MAX_FILES:
        raise HTTPException(status_code=400, detail="Zip file contains too many files")
    
    full_text = []
    for filename in zip_file.namelist():
        # Validate filenames for path traversal/absolute path attacks
        try:
            path = PurePosixPath(filename)
            if path.is_absolute() or '..' in path.parts:
                raise HTTPException(status_code=400, detail="Invalid filename in zip")
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid filename in zip")
            
        if filename.endswith('/'):
            continue  # Skip directories
            
        try:
            with zip_file.open(filename, 'rb') as f:
                content_bytes = f.read()
            text = content_bytes.decode('utf-8')
            full_text.append(text)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid zip file")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="A file in the zip is not a text file")
        except Exception:
            raise HTTPException(status_code=400, detail="An error occurred while processing the zip file")
    
    combined_text = "\n".join(full_text)
    return Response(content=combined_text, media_type="text/plain")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)