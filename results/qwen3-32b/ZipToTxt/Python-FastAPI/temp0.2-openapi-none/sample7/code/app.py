from fastapi import FastAPI, File, UploadFile, HTTPException, Response
import zipfile

app = FastAPI()

@app.post("/convert")
async def convert_zip_to_text(file: UploadFile = File(...)):
    try:
        file.file.seek(0)
        with zipfile.ZipFile(file.file) as zip_ref:
            texts = []
            for zip_info in zip_ref.infolist():
                if zip_info.filename.endswith('/'):
                    continue  # Skip directories
                with zip_ref.open(zip_info) as f:
                    content = f.read()
                try:
                    text = content.decode('utf-8')
                except UnicodeDecodeError:
                    raise HTTPException(status_code=400, detail="Non-text file in zip")
                texts.append(text)
            full_text = '\n'.join(texts)
            return Response(content=full_text, media_type="text/plain")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)