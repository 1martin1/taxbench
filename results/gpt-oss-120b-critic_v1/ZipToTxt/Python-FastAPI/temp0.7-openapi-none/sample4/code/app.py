import io
import zipfile
from typing import List

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile, Response

app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)

# -------------------- Security / Resource limits --------------------
# Maximum size of the uploaded zip file (bytes)
MAX_UPLOAD_SIZE = 10 * 1024 * 1024          # 10 MiB

# Maximum number of *.txt entries to process
MAX_TXT_FILES = 1_000

# Maximum uncompressed size of a single *.txt entry (bytes)
MAX_FILE_UNCOMPRESSED_SIZE = 5 * 1024 * 1024   # 5 MiB

# Maximum total uncompressed size of all *.txt entries (bytes)
MAX_TOTAL_UNCOMPRESSED_SIZE = 50 * 1024 * 1024  # 50 MiB
# -------------------------------------------------------------------


@app.post(
    "/convert",
    summary="Convert a zip file to a text file",
    description=(
        "Convert a zip file into a single text file, concatenating the contents of all "
        "text files in the zip."
    ),
    response_class=Response,
    responses={
        200: {"content": {"text/plain": {}}},
        400: {"description": "Invalid input"},
    },
)
async def convert(file: UploadFile = File(...)):
    """
    Accept a zip file, concatenate the contents of all *.txt files inside,
    and return the combined text as `text/plain`.
    """
    # ---- 1. Enforce upload size limit ----
    # Read up to MAX_UPLOAD_SIZE + 1 bytes to detect overflow.
    raw_bytes = await file.read(MAX_UPLOAD_SIZE + 1)
    if len(raw_bytes) > MAX_UPLOAD_SIZE:
        await file.close()
        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file exceeds the allowed size of {MAX_UPLOAD_SIZE // (1024 * 1024)} MiB.",
        )

    # Ensure the uploaded payload is a zip archive.
    zip_buffer = io.BytesIO(raw_bytes)
    if not zipfile.is_zipfile(zip_buffer):
        await file.close()
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid zip archive.")

    # Reset buffer for ZipFile usage.
    zip_buffer.seek(0)

    concatenated_parts: List[str] = []
    total_uncompressed = 0
    txt_file_count = 0

    try:
        with zipfile.ZipFile(zip_buffer) as zip_ref:
            for zip_info in zip_ref.infolist():
                # Skip directories.
                if zip_info.is_dir():
                    continue

                # Process only .txt files (case‑insensitive).
                if not zip_info.filename.lower().endswith(".txt"):
                    continue

                txt_file_count += 1
                if txt_file_count > MAX_TXT_FILES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"The zip archive contains more than the allowed {MAX_TXT_FILES} text files.",
                    )

                # Enforce per‑file uncompressed size limit.
                if zip_info.file_size > MAX_FILE_UNCOMPRESSED_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"File '{zip_info.filename}' exceeds the per‑file size limit of "
                            f"{MAX_FILE_UNCOMPRESSED_SIZE // (1024 * 1024)} MiB."
                        ),
                    )

                total_uncompressed += zip_info.file_size
                if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"The total uncompressed size of text files exceeds the allowed "
                            f"{MAX_TOTAL_UNCOMPRESSED_SIZE // (1024 * 1024)} MiB."
                        ),
                    )

                # Safely read the file content with an upper bound.
                with zip_ref.open(zip_info) as file_obj:
                    # Read up to the declared size + 1 to detect any discrepancy.
                    raw_content = file_obj.read(zip_info.file_size + 1)
                    if len(raw_content) > zip_info.file_size:
                        raise HTTPException(
                            status_code=400,
                            detail=f"File '{zip_info.filename}' could not be read safely.",
                        )
                    # Decode using UTF‑8 with a fallback to Latin‑1.
                    try:
                        text = raw_content.decode("utf-8")
                    except UnicodeDecodeError:
                        text = raw_content.decode("latin-1")
                    concatenated_parts.append(text)

        # If no .txt files were found, report a client error.
        if not concatenated_parts:
            raise HTTPException(status_code=400, detail="No text files found in the zip archive.")

        # Join parts with a newline separator.
        result_text = "\n".join(concatenated_parts)
        return Response(content=result_text, media_type="text/plain")

    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Bad zip file.")
    finally:
        await file.close()


if __name__ == "__main__":
    # Run the app on 0.0.0.0:5000 as required.
    uvicorn.run(app, host="0.0.0.0", port=5000)