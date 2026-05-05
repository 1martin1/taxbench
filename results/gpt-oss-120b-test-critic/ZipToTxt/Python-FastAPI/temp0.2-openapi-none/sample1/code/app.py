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

# ---------- Security limits ----------
MAX_UPLOAD_SIZE = 10 * 1024 * 1024          # 10 MiB per uploaded zip
MAX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024   # 50 MiB total of extracted data
MAX_TXT_FILES = 1000                       # maximum number of *.txt entries to process
# --------------------------------------


def _extract_texts_from_zip(zip_bytes: bytes) -> str:
    """
    Extract all *.txt files from the given zip archive bytes,
    concatenate their contents (UTF‑8 decoded, ignoring errors) and
    return the combined string.

    The function enforces limits on the number of text files and the
    total uncompressed size to mitigate zip‑bomb attacks.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            # Validate total uncompressed size and file count before reading contents
            total_uncompressed = 0
            txt_infos: List[zipfile.ZipInfo] = []

            for info in z.infolist():
                # Skip directories
                if info.is_dir():
                    continue

                # Only consider .txt files (case‑insensitive)
                if not info.filename.lower().endswith(".txt"):
                    continue

                total_uncompressed += info.file_size
                if total_uncompressed > MAX_UNCOMPRESSED_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail="Uncompressed data size exceeds allowed limit",
                    )

                txt_infos.append(info)
                if len(txt_infos) > MAX_TXT_FILES:
                    raise HTTPException(
                        status_code=400,
                        detail="Number of text files exceeds allowed limit",
                    )

            # Sort filenames for deterministic output
            txt_infos.sort(key=lambda i: i.filename)

            combined_parts: List[str] = []
            for info in txt_infos:
                try:
                    data = z.read(info, pwd=None)
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
        200: {
            "description": "Conversion successful",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        400: {"description": "Invalid input"},
    },
    summary="Convert a zip file to a text file",
    description=(
        "Convert a zip file into a single text file, concatenating the contents "
        "of all text files in the zip."
    ),
)
async def convert(file: UploadFile = File(...)):
    """
    Endpoint that receives a zip file, extracts all *.txt files,
    concatenates their contents and returns the result as plain text.
    """
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Uploaded file is not a zip archive")

    # Read the upload with a size limit to prevent memory exhaustion
    try:
        content = await file.read(MAX_UPLOAD_SIZE + 1)  # read a bit more to detect overflow
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Failed to read uploaded file") from exc
    finally:
        await file.close()

    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file exceeds maximum allowed size of {MAX_UPLOAD_SIZE // (1024 * 1024)} MiB",
        )

    combined_text = _extract_texts_from_zip(content)
    return PlainTextResponse(content=combined_text, media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)