from fastapi import FastAPI, File, UploadFile, HTTPException, Response
import zipfile
import io
import uvicorn

app = FastAPI()

@app.post("/convert")
async def convert_zip_to_text(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail="Invalid input")
    try:
        file_bytes = await file.read()
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zip_file:
            text_contents = []
            for filename in zip_file.namelist():
                if filename.lower().endswith('.txt'):
                    try:
                        with zip_file.open(filename) as text_file:
                            content = text_file.read().decode('utf-8')
                            text_contents.append(content)
                    except (UnicodeDecodeError, zipfile.BadZipFile):
                        continue
            full_text = '\n'.join(text_contents)
            return Response(content=full_text, media_type="text/plain")
    except (zipfile.BadZipFile, zipfile.LargeZipFile):
        raise HTTPException(status_code=400, detail="Invalid input")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)