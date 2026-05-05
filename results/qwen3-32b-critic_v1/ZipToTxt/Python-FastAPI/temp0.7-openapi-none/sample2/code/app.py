from fastapi import FastAPI, UploadFile, File, HTTPException, Response
import zipfile
import io
import asyncio

app = FastAPI()

# Limit concurrent requests to prevent resource exhaustion
semaphore = asyncio.Semaphore(10)

@app.post("/convert")
async def convert_zip_to_text(file: UploadFile = File(..., max_size=10 * 1024 * 1024)):  # 10MB limit
    if not file.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail="File must be a zip file")
    try:
        async with semaphore:
            contents = await file.read()
            with io.BytesIO(contents) as buffer:
                with zipfile.ZipFile(buffer) as zip_ref:
                    texts = []
                    for zip_info in zip_ref.infolist():
                        if zip_info.is_dir():
                            continue
                        # Prevent path traversal and absolute path attacks
                        if zip_info.filename.startswith('/') or '..' in zip_info.filename:
                            continue
                        try:
                            with zip_ref.open(zip_info) as text_file:
                                text = text_file.read().decode('utf-8')
                                texts.append(text)
                        except (zipfile.BadZipFile, UnicodeDecodeError):
                            continue
                    combined_text = '\n\n'.join(texts)
                    return Response(content=combined_text, media_type="text/plain")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")
    except Exception:
        raise HTTPException(status_code=400, detail="An error occurred")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)