import io
import zipfile
import asyncio

from fastapi import FastAPI, UploadFile, File, HTTPException, Response
import uvicorn

app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)

# ---------- Configuration limits ----------
MAX_UPLOAD_SIZE = 10 * 1024 * 1024          # 10 MiB per request
MAX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024   # 50 MiB total uncompressed data
MAX_FILES = 1000                           # Max number of entries processed
MAX_OUTPUT_SIZE = 20 * 1024 * 1024         # 20 MiB maximum response size
# ------------------------------------------


def _read_limited(file_obj: UploadFile, limit: int) -> bytes:
    """
    Read up to ``limit`` bytes from the uploaded file.
    Raises HTTPException(400) if the file exceeds the limit.
    """
    data = file_obj.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status_code=400,
            detail=f"Uploaded file exceeds the allowed size of {limit // (1024 * 1024)} MiB",
        )
    return data


def _process_zip(data: bytes) -> str:
    """
    Synchronous helper that extracts and concatenates *.txt files from a zip archive.
    Enforces limits on number of files, total uncompressed size and output size.
    Raises HTTPException(400) on any policy violation or malformed archive.
    """
    zip_buffer = io.BytesIO(data)

    try:
        with zipfile.ZipFile(zip_buffer) as z:
            # Quick sanity checks before extracting content
            if len(z.infolist()) > MAX_FILES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Zip archive contains more than {MAX_FILES} entries",
                )

            total_uncompressed = 0
            output_fragments = []
            output_size = 0

            for info in z.infolist():
                # Skip directories and non‑text files
                if info.is_dir() or not info.filename.lower().endswith(".txt"):
                    continue

                # Enforce per‑file and total uncompressed size limits
                if info.file_size + total_uncompressed > MAX_UNCOMPRESSED_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail="Uncompressed data size exceeds allowed limit",
                    )
                total_uncompressed += info.file_size

                with z.open(info) as f:
                    # Read the file content (still respecting the size limits)
                    raw = f.read()
                    # Decode safely
                    txt = raw.decode("utf-8", errors="replace")
                    # Track output size (including the newline we will add later)
                    projected_size = output_size + len(txt.encode("utf-8")) + 1  # +1 for newline
                    if projected_size > MAX_OUTPUT_SIZE:
                        raise HTTPException(
                            status_code=400,
                            detail="Resulting output size exceeds allowed limit",
                        )
                    output_fragments.append(txt)
                    output_size = projected_size

            # Join fragments with a newline; the final newline is optional
            combined = "\n".join(output_fragments)
            return combined

    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")
    except HTTPException:
        # Re‑raise our own HTTPException unchanged
        raise
    except Exception as exc:
        # Unexpected errors are logged minimally and reported as a generic client error
        raise HTTPException(status_code=400, detail="Error processing the uploaded file") from exc


@app.post(
    "/convert",
    response_class=Response,
    responses={
        200: {
            "description": "Conversion successful",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        400: {"description": "Invalid input"},
    },
)
async def convert_zip(file: UploadFile = File(...)):
    """
    Convert a zip archive into a single plain‑text response.
    All *.txt files inside the zip are concatenated (in the order they appear
    in the archive) and separated by a newline character.
    """
    # Basic validation – ensure the uploaded file *looks* like a zip archive.
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a .zip archive")

    # Enforce upload size limit
    raw_bytes = _read_limited(file, MAX_UPLOAD_SIZE)

    # Offload the potentially CPU‑intensive zip processing to a thread pool
    combined_text = await asyncio.to_thread(_process_zip, raw_bytes)

    return Response(content=combined_text, media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)