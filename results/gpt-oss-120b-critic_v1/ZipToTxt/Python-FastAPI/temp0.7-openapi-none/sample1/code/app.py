import asyncio
import io
import zipfile
import tempfile
from typing import List

from fastapi import FastAPI, File, HTTPException, UploadFile, Response
import uvicorn

app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)

# ---------- Configuration ----------
MAX_UPLOAD_SIZE = 10 * 1024 * 1024          # 10 MiB per uploaded file
MAX_FILES_IN_ZIP = 100                     # Maximum number of entries processed
MAX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024   # 50 MiB total uncompressed data
# -----------------------------------


def _extract_texts_from_zip(zip_file_obj: io.BytesIO | tempfile.SpooledTemporaryFile) -> str:
    """
    Extracts and concatenates the content of all *.txt files in the provided zip archive.

    The function enforces limits on the number of entries and the total
    uncompressed size to mitigate zip‑bomb attacks.

    Args:
        zip_file_obj: A file‑like object containing the zip data.

    Returns:
        A single string containing the concatenated text of all *.txt files,
        separated by a newline.

    Raises:
        HTTPException: If the zip file is malformed or violates any limits.
    """
    try:
        with zipfile.ZipFile(zip_file_obj) as z:
            txt_contents: List[str] = []
            total_uncompressed = 0
            processed_files = 0

            for info in z.infolist():
                if info.is_dir():
                    continue

                processed_files += 1
                if processed_files > MAX_FILES_IN_ZIP:
                    raise HTTPException(
                        status_code=400,
                        detail="Zip archive contains too many files",
                    )

                total_uncompressed += info.file_size
                if total_uncompressed > MAX_UNCOMPRESSED_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail="Zip archive uncompressed size exceeds limit",
                    )

                if not info.filename.lower().endswith(".txt"):
                    continue

                with z.open(info) as file:
                    data = file.read().decode("utf-8", errors="replace")
                    txt_contents.append(data)

            return "\n".join(txt_contents)

    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid zip file") from exc
    except (zipfile.LargeZipFile, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=400, detail="Invalid zip file") from exc


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
async def convert(file: UploadFile = File(...)):
    """
    Endpoint that receives a zip file, extracts all *.txt files,
    concatenates their contents, and returns the result as plain text.
    """
    # Enforce a reasonable upload size limit
    total_read = 0
    spooled = tempfile.SpooledTemporaryFile(max_size=5 * 1024 * 1024)  # 5 MiB in memory, then disk

    try:
        while True:
            chunk = await file.read(1024 * 1024)  # read in 1 MiB chunks
            if not chunk:
                break
            total_read += len(chunk)
            if total_read > MAX_UPLOAD_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail="Uploaded file exceeds size limit",
                )
            spooled.write(chunk)

        if total_read == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        spooled.seek(0)

        # Quick magic‑number validation (PK\x03\x04) to reject obvious non‑zip files early
        header = spooled.read(4)
        spooled.seek(0)
        if header != b"PK\x03\x04":
            raise HTTPException(
                status_code=400,
                detail="Uploaded file is not a valid zip archive",
            )

        # Perform the potentially blocking zip processing in a thread pool
        combined_text = await asyncio.to_thread(_extract_texts_from_zip, spooled)

        return Response(content=combined_text, media_type="text/plain")

    finally:
        spooled.close()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)