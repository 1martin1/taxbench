import io
import zipfile
from fastapi import FastAPI, File, UploadFile, HTTPException, Response

app = FastAPI()

@app.post("/convert")
async def convert_zip_to_text(file: UploadFile = File(...)):
    try:
        file_bytes = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")

    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zip_file:
            all_text = ""
            for file_info in zip_file.infolist():
                if file_info.is_dir():
                    continue
                if file_info.filename.lower().endswith('.txt'):
                    try:
                        with zip_file.open(file_info) as f:
                            content = f.read().decode('utf-8')
                            all_text += content + '\n'
                    except (zipfile.BadZipFile, UnicodeDecodeError):
                        raise HTTPException(status_code=400, detail="Invalid input")
            if not all_text:
                raise HTTPException(status_code=400, detail="No text files found in zip")
            return Response(content=all_text, media_type="text/plain")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)