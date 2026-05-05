import zipfile
import io
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import Response

app = FastAPI()

@app.post("/convert")
async def convert_zip_to_text(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        zip_data = io.BytesIO(contents)
        with zipfile.ZipFile(zip_data) as zip_file:
            text_contents = []
            for info in zip_file.infolist():
                if info.is_dir():
                    continue
                try:
                    with zip_file.open(info) as f:
                        text = f.read().decode('utf-8')
                        text_contents.append(text)
                except UnicodeDecodeError:
                    continue
            return Response(content="\n".join(text_contents), media_type="text/plain")
    except zipfile.BadZipFile:
        return Response(content="Invalid zip file", status_code=400, media_type="text/plain")
    except Exception as e:
        return Response(content=f"Error: {str(e)}", status_code=400, media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)