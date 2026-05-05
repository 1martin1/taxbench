import zipfile
import io
from fastapi import FastAPI, File, UploadFile, HTTPException, Response

app = FastAPI()

def is_text_file(filename: str) -> bool:
    text_extensions = {
        '.txt', '.text', '.csv', '.log', '.md', '.rst', '.json', '.xml',
        '.yaml', '.yml', '.html', '.htm', '.css', '.js', '.sql', '.ini',
        '.cfg', '.conf', '.sh', '.bat', '.ps1', '.tex', '.tsv', '.lock',
        '.env', '.toml'
    }
    return any(filename.lower().endswith(ext) for ext in text_extensions)

@app.post("/convert")
async def convert_zip_to_text(file: UploadFile = File(...)):
    content = await file.read()
    
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zip_file:
            text_files_content = []
            for zip_info in zip_file.infolist():
                if zip_info.is_dir():
                    continue
                filename = zip_info.filename
                if not is_text_file(filename):
                    continue
                try:
                    with zip_file.open(zip_info) as f:
                        text = f.read().decode('utf-8')
                    text_files_content.append(text)
                except Exception:
                    continue
            combined_text = '\n'.join(text_files_content)
            return Response(content=combined_text, media_type="text/plain")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")
    except Exception:
        raise HTTPException(status_code=400, detail="Error processing zip file")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)