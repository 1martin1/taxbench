import os
import subprocess
import tempfile
from typing import Optional

import anyio
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import PlainTextResponse

# -------------------- Configuration --------------------
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB
PDftotext_TIMEOUT = 30  # seconds
MAX_CONCURRENT_CONVERSIONS = 5  # simple rate‑limiting via concurrency control
# -------------------------------------------------------

app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)

# Semaphore to limit concurrent conversions (basic rate limiting)
conversion_semaphore = anyio.Semaphore(MAX_CONCURRENT_CONVERSIONS)


def _run_pdftotext(pdf_path: str) -> str:
    """
    Execute the `pdftotext` command on the given PDF file and return the extracted text.
    A timeout is applied to avoid hanging processes.
    """
    try:
        result = subprocess.run(
            ["pdftotext", pdf_path, "-"],
            capture_output=True,
            text=True,
            check=False,
            timeout=PDftotext_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("pdftotext timed out.") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("pdftotext utility not found on the system.") from exc

    if result.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {result.stderr.strip()}")
    return result.stdout


def _is_valid_pdf_header(header_bytes: bytes) -> bool:
    """
    Very basic validation that the file starts with the PDF magic number.
    """
    # Allow leading whitespace before %PDF
    stripped = header_bytes.lstrip()
    return stripped.startswith(b"%PDF")


@app.post(
    "/convert",
    response_class=PlainTextResponse,
    responses={
        200: {"description": "Successfully converted PDF to text"},
        400: {"description": "Bad Request - Invalid input or missing file"},
        500: {"description": "Internal Server Error - Conversion failed"},
    },
    summary="Convert a PDF file to Text",
    description="Upload a PDF file to convert it into Text format.",
)
async def convert(request: Request, file: UploadFile = File(...)):
    """
    Receive a PDF file, validate it, convert it to plain text, and return the result.
    """
    # Basic presence check (FastAPI already ensures it's provided)
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided.",
        )

    # Basic content‑type validation (helps clients, but not security‑critical)
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is not a PDF.",
        )

    # Enforce size limit and minimal PDF validation while streaming to a temp file
    total_bytes = 0
    tmp_path: Optional[str] = None

    # Read the first chunk separately to validate PDF header
    first_chunk = await file.read(1024)  # 1 KiB for header inspection
    if not first_chunk:
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )
    if not _is_valid_pdf_header(first_chunk):
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is not a valid PDF.",
        )
    total_bytes += len(first_chunk)
    if total_bytes > MAX_UPLOAD_SIZE:
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size exceeds the allowed limit.",
        )

    # Acquire semaphore to limit concurrent conversions (basic rate limiting)
    async with conversion_semaphore:
        try:
            # Create a temporary file to store the uploaded PDF
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                tmp_path = tmp_file.name
                tmp_file.write(first_chunk)

                # Continue reading the rest of the file in chunks
                while True:
                    chunk = await file.read(1024 * 1024)  # 1 MiB chunks
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > MAX_UPLOAD_SIZE:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="File size exceeds the allowed limit.",
                        )
                    tmp_file.write(chunk)

            # At this point the entire file is saved; run the conversion
            try:
                extracted_text = _run_pdftotext(tmp_path)
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=str(exc),
                )
        finally:
            # Ensure the uploaded file stream is closed
            await file.close()
            # Clean up the temporary file if it was created
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    return PlainTextResponse(content=extracted_text)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)