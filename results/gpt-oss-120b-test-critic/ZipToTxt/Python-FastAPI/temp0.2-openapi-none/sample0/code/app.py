import io
import zipfile

from fastapi import FastAPI, File, HTTPException, UploadFile, Response
import uvicorn

app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)

# Security / resource limits
MAX_UPLOAD_SIZE = 10 * 1024 * 1024          # 10 MiB per uploaded archive
MAX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024   # 50 MiB total uncompressed data
MAX_FILES_IN_ZIP = 1000                    # maximum number of entries processed


@app.post(
    "/convert",
    response_class=Response,
    responses={
        200: {"content": {"text/plain": {}}},
        400: {"description": "Invalid input"},
    },
)
async def convert_zip_to_text(file: UploadFile = File(...)):
    """
    Convert a zip file into a single text file, concatenating the contents of all
    `.txt` files contained in the archive.

    The endpoint validates the uploaded archive size, the total uncompressed
    size, and the number of entries to mitigate resource‑exhaustion attacks.
    """
    # --------------------------------------------------------------
    # 1. Enforce upload size limit while reading the file.
    # --------------------------------------------------------------
    total_read = 0
    chunks = []
    try:
        while True:
            chunk = await file.read(1024 * 1024)  # read in 1 MiB blocks
            if not chunk:
                break
            total_read += len(chunk)
            if total_read > MAX_UPLOAD_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail="Uploaded file exceeds the allowed size limit.",
                )
            chunks.append(chunk)
    finally:
        # Ensure the underlying temporary file is closed promptly.
        await file.close()

    file_bytes = b"".join(chunks)
    zip_buffer = io.BytesIO(file_bytes)

    # --------------------------------------------------------------
    # 2. Process the zip archive safely.
    # --------------------------------------------------------------
    try:
        with zipfile.ZipFile(zip_buffer) as z:
            total_uncompressed = 0
            processed_files = 0
            texts = []

            for info in z.infolist():
                # Skip directories.
                if info.is_dir():
                    continue

                processed_files += 1
                if processed_files > MAX_FILES_IN_ZIP:
                    raise HTTPException(
                        status_code=400,
                        detail="Zip archive contains too many files.",
                    )

                total_uncompressed += info.file_size
                if total_uncompressed > MAX_UNCOMPRESSED_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail="Uncompressed data exceeds the allowed size limit.",
                    )

                # Only concatenate `.txt` files (case‑insensitive).
                if not info.filename.lower().endswith(".txt"):
                    continue

                with z.open(info) as f:
                    raw_content = f.read()
                    # Decode using UTF‑8, replace undecodable bytes.
                    text = raw_content.decode("utf-8", errors="replace")
                    texts.append(text)

            combined_text = "\n".join(texts)

    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid zip archive.")
    except HTTPException:
        # Re‑raise our own HTTP errors unchanged.
        raise
    except Exception:
        # Generic error handling without leaking internal details.
        raise HTTPException(status_code=400, detail="Failed to process the uploaded zip file.")

    return Response(content=combined_text, media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)