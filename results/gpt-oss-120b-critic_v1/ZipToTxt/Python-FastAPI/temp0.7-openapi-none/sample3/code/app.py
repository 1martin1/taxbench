import io
import zipfile
from typing import List

import anyio
from fastapi import FastAPI, File, HTTPException, UploadFile, Response
import uvicorn

app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)

# ---- Security / resource limits ----
MAX_UPLOAD_SIZE = 50 * 1024 * 1024          # 50 MiB
MAX_FILES_IN_ZIP = 1_000                    # Max entries in the archive
MAX_UNCOMPRESSED_SIZE = 200 * 1024 * 1024   # 200 MiB total uncompressed payload
MAX_COMPRESSION_RATIO = 100                 # Uncompressed / compressed size


def _is_path_safe(name: str) -> bool:
    """
    Ensure the zip entry does not attempt path traversal.
    """
    # Normalise to forward slashes for consistency
    parts = name.replace("\\", "/").split("/")
    return not (".." in parts or name.startswith("/") or name.startswith("\\"))
    

def _process_zip(file_obj) -> str:
    """
    Synchronous helper that extracts *.txt files from a zip archive,
    applies resource limits, and returns the concatenated text.
    """
    # Ensure we read from the beginning
    file_obj.seek(0)

    with zipfile.ZipFile(file_obj) as z:
        # Basic zip‑bomb protections
        if len(z.infolist()) > MAX_FILES_IN_ZIP:
            raise ValueError("Zip archive contains too many entries.")

        total_uncompressed = 0
        txt_parts: List[str] = []

        for info in z.infolist():
            # Skip directories
            if info.is_dir():
                continue

            # Skip unsafe paths
            if not _is_path_safe(info.filename):
                continue

            # Process only .txt files (case‑insensitive)
            if not info.filename.lower().endswith(".txt"):
                continue

            # Enforce per‑entry compression ratio
            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > MAX_COMPRESSION_RATIO:
                    raise ValueError(
                        f"Compression ratio too high for entry {info.filename!r}."
                    )

            # Accumulate uncompressed size
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_UNCOMPRESSED_SIZE:
                raise ValueError("Total uncompressed size exceeds allowed limit.")

            # Read file content
            with z.open(info) as f:
                raw = f.read()
                txt_parts.append(raw.decode("utf-8", errors="ignore"))

        # Join with a newline; empty result is permissible
        return "\n".join(txt_parts)


@app.post(
    "/convert",
    summary="Convert a zip file to a text file",
    description=(
        "Convert a zip file into a single text file, concatenating the contents "
        "of all text files in the zip."
    ),
    response_class=Response,
    responses={
        200: {
            "description": "Conversion successful",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        400: {"description": "Invalid input"},
        500: {"description": "Internal server error"},
    },
)
async def convert(file: UploadFile = File(...)):
    """
    Endpoint that receives a zip archive, validates it, and returns the
    concatenated text of all *.txt files inside.
    """
    # Basic file‑type check
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Uploaded file is not a zip archive.")

    # Optional upload‑size guard using the Content‑Length header
    content_length = file.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_UPLOAD_SIZE:
                raise HTTPException(status_code=400, detail="Uploaded file is too large.")
        except (ValueError, TypeError):
            # If header is malformed, fall back to processing (will be limited later)
            pass

    try:
        # Run the potentially blocking zip processing in a thread pool
        combined_text = await anyio.to_thread.run_sync(_process_zip, file.file)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file.")
    except ValueError as ve:
        # Raised for our custom limit violations
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        # Catch‑all for unexpected errors; do not expose internal details
        raise HTTPException(status_code=500, detail="Failed to process the zip file.")

    return Response(content=combined_text, media_type="text/plain")


if __name__ == "__main__":
    # Run the app on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000)