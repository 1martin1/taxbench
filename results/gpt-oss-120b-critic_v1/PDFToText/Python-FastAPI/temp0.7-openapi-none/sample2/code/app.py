import asyncio
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.responses import Response

app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)

# Configuration constants
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MiB
PDftotext_TIMEOUT = 15  # seconds


@app.post(
    "/convert",
    responses={
        200: {
            "description": "Successfully converted PDF to text",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        400: {"description": "Bad Request - Invalid input or missing file"},
        500: {"description": "Internal Server Error - Conversion failed"},
    },
)
async def convert_pdf_to_text(file: UploadFile = File(...)):
    """
    Convert an uploaded PDF file to plain text using the `pdftotext` utility.
    """
    # Basic file‑type validation
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is not a PDF.",
        )

    # Read the uploaded content with size limit
    try:
        content = await file.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read uploaded file.",
        ) from exc

    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File size exceeds the allowed limit of {MAX_UPLOAD_SIZE // (1024 * 1024)} MiB.",
        )

    # Store the file in a temporary location
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(content)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store uploaded file.",
        ) from exc

    # Run pdftotext asynchronously with a timeout
    try:
        proc = await asyncio.create_subprocess_exec(
            "pdftotext",
            "-layout",
            str(tmp_path),
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=PDftotext_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Conversion process timed out.",
            )
    finally:
        # Ensure the temporary file is removed
        if tmp_path and tmp_path.is_file():
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # Handle conversion result
    if proc.returncode != 0:
        # Treat non‑zero exit as a client error (e.g., invalid PDF)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to convert PDF to text. The file may be corrupted or not a valid PDF.",
        )

    return Response(content=stdout, media_type="text/plain")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)