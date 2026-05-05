import io
import zipfile
from typing import List

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)


def _extract_texts_from_zip(zip_bytes: bytes) -> str:
    """
    Extract all *.txt files from the given zip archive bytes,
    concatenate their contents (UTF‑8 decoded, ignoring errors) and
    return the combined string.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            # Get a sorted list of file names to have deterministic order
            txt_names: List[str] = sorted(
                [name for name in z.namelist() if not name.endswith("/") and name.lower().endswith(".txt")]
            )
            combined_parts: List[str] = []
            for name in txt_names:
                try:
                    data = z.read(name)
                    text = data.decode("utf-8", errors="ignore")
                    combined_parts.append(text)
                except Exception:
                    # If a particular file cannot be read/decoded, skip it
                    continue
            return "\n".join(combined_parts)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid zip file") from exc


@app.post(
    "/convert",
    response_class=PlainTextResponse,
    responses={
        200: {"description": "Conversion successful", "content": {"text/plain": {"schema": {"type": "string"}}}},
        400: {"description": "Invalid input"},
    },
    summary="Convert a zip file to a text file",
    description="Convert a zip file into a single text file, concatenating the contents of all text files in the zip.",
)
async def convert(file: UploadFile = File(...)):
    """
    Endpoint that receives a zip file, extracts all *.txt files,
    concatenates their contents and returns the result as plain text.
    """
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Uploaded file is not a zip archive")

    try:
        zip_bytes = await file.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Failed to read uploaded file") from exc

    combined_text = _extract_texts_from_zip(zip_bytes)
    return PlainTextResponse(content=combined_text, media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)