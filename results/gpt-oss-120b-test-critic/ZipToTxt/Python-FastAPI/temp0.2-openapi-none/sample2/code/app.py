import io
import zipfile
from typing import List

from fastapi import FastAPI, File, HTTPException, UploadFile, Response
import uvicorn

app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)

# Security limits
MAX_UPLOAD_SIZE = 10 * 1024 * 1024          # 10 MiB for the uploaded archive
MAX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024   # 50 MiB total uncompressed data
MAX_TOTAL_FILES = 1000                     # Prevent processing an excessive number of entries


@app.post(
    "/convert",
    summary="Convert a zip file to a text file",
    description="Convert a zip file into a single text file, concatenating the contents of all text files in the zip.",
    response_class=Response,
    responses={
        200: {
            "description": "Conversion successful",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        400: {"description": "Invalid input"},
    },
)
async def convert_zip_to_txt(file: UploadFile = File(...)):
    """
    Accept a zip file, extract all *.txt files, concatenate their contents,
    and return the combined text as `text/plain`.
    """
    # Basic MIME type check – not strictly required but adds a layer of validation
    if file.content_type not in ("application/zip", "application/x-zip-compressed", "multipart/x-zip"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a zip archive")

    # Verify filename extension as an additional hint (do not rely on it for security)
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Uploaded file must have a .zip extension")

    # Read the uploaded file into memory (FastAPI stores it in a SpooledTemporaryFile)
    try:
        await file.seek(0)
        file_bytes = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read uploaded file")

    # Enforce upload size limit
    if len(file_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file exceeds the maximum allowed size of {MAX_UPLOAD_SIZE // (1024 * 1024)} MiB",
        )

    zip_bytes = io.BytesIO(file_bytes)

    try:
        with zipfile.ZipFile(zip_bytes) as zip_ref:
            # Basic sanity checks on the archive contents
            if len(zip_ref.infolist()) > MAX_TOTAL_FILES:
                raise HTTPException(
                    status_code=400,
                    detail="Zip archive contains too many entries",
                )

            total_uncompressed = sum(info.file_size for info in zip_ref.infolist())
            if total_uncompressed > MAX_UNCOMPRESSED_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail="Uncompressed data size of the zip archive exceeds the allowed limit",
                )

            # Gather .txt files (case‑insensitive) and sort them for deterministic output
            txt_names: List[str] = sorted(
                (
                    info.filename
                    for info in zip_ref.infolist()
                    if not info.is_dir() and info.filename.lower().endswith(".txt")
                ),
                key=lambda x: x.lower(),
            )

            if not txt_names:
                combined_text = ""
            else:
                parts: List[str] = []
                for name in txt_names:
                    with zip_ref.open(name) as f:
                        content_bytes = f.read()
                        # Decode using UTF‑8, replace undecodable bytes to avoid errors
                        content_str = content_bytes.decode("utf-8", errors="replace")
                        parts.append(content_str)
                combined_text = "\n".join(parts)

    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid zip archive")
    except HTTPException:
        # Re‑raise our own HTTPExceptions unchanged
        raise
    except Exception:
        # Do not expose internal details
        raise HTTPException(status_code=400, detail="An error occurred while processing the zip file")

    return Response(content=combined_text, media_type="text/plain")


if __name__ == "__main__":
    # Run the app on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000)