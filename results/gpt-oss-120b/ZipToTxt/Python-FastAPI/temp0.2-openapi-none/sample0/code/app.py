import io
import zipfile
from fastapi import FastAPI, File, UploadFile, HTTPException, Response
import uvicorn

app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)


@app.post("/convert", response_class=Response, responses={200: {"content": {"text/plain": {}}}, 400: {"description": "Invalid input"}})
async def convert_zip_to_text(file: UploadFile = File(...)):
    """
    Convert a zip file into a single text file, concatenating the contents of all text files in the zip.
    """
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Uploaded file is not a zip archive.")

    try:
        # Read the uploaded file into memory
        file_bytes = await file.read()
        zip_bytes = io.BytesIO(file_bytes)

        with zipfile.ZipFile(zip_bytes) as z:
            # Gather text from all .txt files (case‑insensitive)
            texts = []
            for info in z.infolist():
                # Skip directories
                if info.is_dir():
                    continue
                # Process only .txt files
                if not info.filename.lower().endswith(".txt"):
                    continue
                with z.open(info) as f:
                    # Decode as UTF‑8, replace errors to avoid crashes on unknown encodings
                    content = f.read().decode("utf-8", errors="replace")
                    texts.append(content)

            # If no text files were found, return empty string (still a 200)
            combined_text = "\n".join(texts)

    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file.")
    except Exception as e:
        # Unexpected errors are treated as bad request for simplicity
        raise HTTPException(status_code=400, detail=str(e))

    return Response(content=combined_text, media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)