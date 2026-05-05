from fastapi import FastAPI, File, UploadFile, HTTPException, Response
import zipfile
import io
import asyncio

app = FastAPI()

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_FILES = 1000
MAX_TOTAL_TEXT_SIZE = 50 * 1024 * 1024  # 50MB
MAX_FILE_TEXT_SIZE = 5 * 1024 * 1024  # 5MB per file
TIMEOUT_SECONDS = 30

@app.post("/convert")
async def convert_zip_to_text(file: UploadFile = File(..., max_size=MAX_FILE_SIZE)):
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            contents = await file.read()
            zip_data = io.BytesIO(contents)
            
            try:
                with zipfile.ZipFile(zip_data) as zip_ref:
                    files = zip_ref.namelist()
                    if len(files) > MAX_FILES:
                        raise HTTPException(status_code=400, detail="Too many files in ZIP")
                    
                    text_contents = []
                    total_size = 0
                    
                    for filename in files:
                        try:
                            with zip_ref.open(filename) as f:
                                text_bytes = f.read()
                                
                                # Skip files that are too large
                                if len(text_bytes) > MAX_FILE_TEXT_SIZE:
                                    continue
                                    
                                try:
                                    text = text_bytes.decode('utf-8')
                                except UnicodeDecodeError:
                                    continue
                                    
                                # Check total size limit
                                if total_size + len(text_bytes) > MAX_TOTAL_TEXT_SIZE:
                                    raise HTTPException(status_code=400, detail="Total text size exceeded")
                                    
                                text_contents.append(text)
                                total_size += len(text_bytes)
                                
                        except (zipfile.BadZipFile, OSError):
                            continue
                    
                    full_text = '\n'.join(text_contents)
                    return Response(content=full_text.encode('utf-8'), media_type='text/plain')
                    
            except (zipfile.BadZipFile, OSError):
                raise HTTPException(status_code=400, detail="Invalid input")
                
    except asyncio.TimeoutError:
        raise HTTPException(status_code=400, detail="Processing timed out")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)