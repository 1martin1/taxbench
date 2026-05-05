from fastapi import FastAPI, UploadFile, File, Response, HTTPException
import zipfile

app = FastAPI()

@app.post("/convert")
async def convert_zip_to_text(file: UploadFile = File(...)):
    try:
        with zipfile.ZipFile(file.file) as zip_file:
            text_content = []
            for name in zip_file.namelist():
                if name.lower().endswith('.txt'):
                    try:
                        content = zip_file.read(name)
                    except (RuntimeError, zipfile.BadZipFile):
                        continue
                    try:
                        text = content.decode('utf-8')
                    except UnicodeDecodeError:
                        raise HTTPException(status_code=400, detail="Invalid text file encoding")
                    text_content.append(text)
            full_text = ''.join(text_content)
            return Response(content=full_text, media_type="text/plain")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)