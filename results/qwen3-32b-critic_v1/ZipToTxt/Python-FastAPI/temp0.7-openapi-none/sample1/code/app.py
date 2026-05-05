from fastapi import FastAPI, File, UploadFile, HTTPException, Response
from fastapi.concurrency import to_thread
import zipfile
import io
import asyncio

app = FastAPI()

# Security/Robustness Limits
MAX_ZIP_SIZE = 10 * 1024 * 1024  # 10MB
MAX_FILES_IN_ZIP = 100
MAX_PER_FILE_SIZE = 1 * 1024 * 1024  # 1MB
PROCESSING_TIMEOUT = 30  # seconds

@app.post("/convert")
async def convert_zip_to_text(file: UploadFile = File(...)):
    contents = await file.read()

    # Size limit check
    if len(contents) > MAX_ZIP_SIZE:
        raise HTTPException(status_code=400, detail="Uploaded zip file exceeds maximum size of 10MB")

    try:
        async with asyncio.timeout(PROCESSING_TIMEOUT):
            combined_text = await to_thread.run_sync(process_zip, contents)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=400, detail="Zip processing timeout")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Non-text file in zip")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e

    return Response(content=combined_text, media_type="text/plain")

def process_zip(contents: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(contents)) as zip_ref:
        file_list = zip_ref.infolist()
        if len(file_list) > MAX_FILES_IN_ZIP:
            raise ValueError(f"Zip contains too many files (max {MAX_FILES_IN_ZIP})")

        text_contents = []
        for file_info in file_list:
            if not file_info.is_dir() and file_info.filename.lower().endswith('.txt'):
                try:
                    text = read_text_file(zip_ref, file_info, MAX_PER_FILE_SIZE)
                    text_contents.append(text)
                except UnicodeDecodeError:
                    raise
        if not text_contents:
            raise ValueError("No text files in zip")
        return ''.join(text_contents)

def read_text_file(zip_ref: zipfile.ZipFile, file_info: zipfile.ZipInfo, max_size: int) -> str:
    current_size = 0
    text_parts = []
    with zip_ref.open(file_info) as f:
        while True:
            chunk = f.read(4096)
            if not chunk:
                break
            current_size += len(chunk)
            if current_size > max_size:
                raise ValueError(f"Text file {file_info.filename} exceeds maximum size of {max_size / 1024 / 1024}MB")
            text_parts.append(chunk.decode('utf-8'))
    return ''.join(text_parts)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)