import io
import zipfile
from typing import List

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
import uvicorn

app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)

# ---------- Security / resource limits ----------
MAX_UPLOAD_SIZE = 10 * 1024 * 1024               # 10 MiB per uploaded zip
MAX_TOTAL_UNCOMPRESSED = 50 * 1024 * 1024       # 50 MiB total uncompressed data
MAX_TXT_FILES = 1000                            # maximum number of .txt entries
MAX_TXT_FILE_SIZE = 5 * 1024 * 1024             # 5 MiB per .txt file
MAX_COMPRESSION_RATIO = 100                     # reject entries that expand >100×


def _is_safe_path(name: str) -> bool:
    """
    Ensure the zip entry name does not attempt path traversal.
    """
    # Normalise path separators and prevent absolute paths or '..' segments
    if name.startswith("/") or ".." in name.split("/"):
        return False
    return True


def _extract_text_files(zip_bytes: bytes) -> str:
    """
    Extract all *.txt files from the provided zip archive bytes,
    concatenate their contents (alphabetically), and return the combined text.

    Performs strict validation to mitigate zip‑bomb and resource‑exhaustion attacks.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            # Validate entries before extraction
            txt_infos: List[zipfile.ZipInfo] = []
            total_uncompressed = 0

            for info in zf.infolist():
                # Skip directories
                if info.is_dir():
                    continue

                # Reject unsafe paths
                if not _is_safe_path(info.filename):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unsafe file path detected in archive: {info.filename}",
                    )

                # Only consider .txt files (case‑insensitive)
                if not info.filename.lower().endswith(".txt"):
                    continue

                # Enforce per‑file size limit (using uncompressed size)
                if info.file_size > MAX_TXT_FILE_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f".txt file too large: {info.filename}",
                    )

                # Enforce compression ratio limit
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > MAX_COMPRESSION_RATIO:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Potential zip bomb detected in file: {info.filename}",
                        )

                txt_infos.append(info)
                total_uncompressed += info.file_size

                # Early abort if limits exceeded
                if len(txt_infos) > MAX_TXT_FILES:
                    raise HTTPException(
                        status_code=400,
                        detail="Too many .txt files in archive",
                    )
                if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
                    raise HTTPException(
                        status_code=400,
                        detail="Total uncompressed size of .txt files exceeds limit",
                    )

            # Sort entries alphabetically for deterministic output
            txt_infos.sort(key=lambda i: i.filename.lower())

            combined_parts: List[str] = []
            for info in txt_infos:
                try:
                    with zf.open(info, "r") as f:
                        # Read in chunks to avoid loading huge files at once
                        # (still respects the per‑file size limit enforced above)
                        raw_bytes = f.read()
                        text = raw_bytes.decode("utf-8", errors="replace")
                        combined_parts.append(text)
                except (RuntimeError, OSError, zipfile.BadZipFile) as e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Failed to read file {info.filename}: {str(e)}",
                    )

            return "".join(combined_parts)

    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid zip archive")
    except HTTPException:
        # Re‑raise our own controlled errors
        raise
    except Exception as e:
        # Catch‑all for unexpected zip handling errors
        raise HTTPException(status_code=400, detail=f"Invalid zip archive: {str(e)}")


@app.post(
    "/convert",
    summary="Convert a zip file to a text file",
    description="Convert a zip file into a single text file, concatenating the contents of all text files in the zip.",
    responses={
        200: {
            "description": "Conversion successful",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        400: {"description": "Invalid input"},
    },
)
async def convert(file: UploadFile = File(...)):
    """
    Endpoint that receives a zip file, extracts all *.txt files,
    concatenates their contents and returns the result as plain text.
    """
    if not file:
        raise HTTPException(status_code=400, detail="File part is missing")

    # Enforce upload size limit while reading
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file exceeds maximum allowed size of {MAX_UPLOAD_SIZE // (1024*1024)} MiB",
        )

    combined_text = _extract_text_files(content)

    return Response(content=combined_text, media_type="text/plain")


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000)