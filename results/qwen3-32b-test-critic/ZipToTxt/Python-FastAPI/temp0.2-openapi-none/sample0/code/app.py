import zipfile
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import PlainTextResponse
import uvicorn

app = FastAPI()

@app.post("/convert")
async def convert_zip_to_text(file: UploadFile = File(...)):
    try:
        file.file.seek(0)
        with zipfile.ZipFile(file.file, 'r') as zip_file:
            text_content = []
            for filename in zip_file.namelist():
                if filename.endswith('.txt'):
                    with zip_file.open(filename) as f:
                        content = f.read().decode('utf-8')
                        text_content.append(content)
            return PlainTextResponse("\n".join(text_content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid input: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)