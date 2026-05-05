from fastapi import FastAPI, File, UploadFile, Response, HTTPException
import zipfile

app = FastAPI()

@app.post("/convert")
async def convert_zip_to_text(file: UploadFile = File(...)):
    try:
        text_contents = []
        with zipfile.ZipFile(file.file, 'r') as zip_ref:
            for zip_info in zip_ref.infolist():
                if zip_info.filename.lower().endswith('.txt'):
                    with zip_ref.open(zip_info) as txt_file:
                        content = txt_file.read().decode('utf-8')
                        text_contents.append(content)
        return Response(content=''.join(text_contents), media_type='text/plain')
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)