import os
import asyncio
import subprocess
import tempfile
import shutil
from typing import List

from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse, Response
import uvicorn

app = FastAPI(title="PDF Concatenation Service", version="1.0.0")

# ---------- Configuration ----------
MAX_FILES = 10                     # Maximum number of files per request
MAX_FILE_SIZE = 10 * 1024 * 1024   # 10 MiB per file
MAX_TOTAL_SIZE = 50 * 1024 * 1024  # 50 MiB total
PDFUNITE_TIMEOUT = 30             # seconds
# -----------------------------------


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Return errors in the format required by the OpenAPI spec:
    { "error": "<message>" }
    """
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """
    Catch‑all handler to ensure all error responses follow the spec.
    """
    # Log the exception here if a logging system is in place.
    return JSONResponse(
        status_code=500,
        content={"error": "An error occurred while processing the files."},
    )


def copy_with_limit(src, dst, max_bytes: int) -> int:
    """
    Copy data from src to dst while enforcing a maximum number of bytes.
    Returns the total number of bytes copied.
    Raises ValueError if the limit is exceeded.
    """
    total = 0
    while True:
        chunk = src.read(8192)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("File size exceeds the allowed limit.")
        dst.write(chunk)
    return total


async def save_upload_to_temp(upload: UploadFile, max_file_size: int) -> str:
    """
    Validate that the uploaded file is a PDF by checking the magic header,
    then stream it to a temporary file while enforcing a size limit.
    Returns the path to the temporary file.
    """
    # Reset pointer (UploadFile.file is a SpooledTemporaryFile)
    upload.file.seek(0)

    # Read first 4 bytes to verify PDF magic number
    header = upload.file.read(4)
    upload.file.seek(0)
    if not header.startswith(b"%PDF"):
        raise HTTPException(
            status_code=400,
            detail="All uploaded files must be valid PDFs.",
        )

    # Stream to temporary file with size enforcement
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_out:
        copied_bytes = copy_with_limit(upload.file, tmp_out, max_file_size)
        tmp_out.flush()
        return tmp_out.name, copied_bytes


@app.post(
    "/concatenate",
    responses={
        200: {
            "description": "Successfully concatenated the PDF files.",
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
        },
        400: {
            "description": "Bad Request - Invalid input or missing files.",
            "content": {"application/json": {"example": {"error": "Invalid input or missing files."}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while processing the files.",
            "content": {"application/json": {"example": {"error": "An error occurred while processing the files."}}},
        },
    },
)
async def concatenate(files: List[UploadFile] = File(...)):
    """
    Accept multiple PDF files and return a single concatenated PDF.
    """
    if not files or len(files) == 0:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")

    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files. Maximum allowed is {MAX_FILES}.",
        )

    input_paths: List[str] = []
    total_bytes = 0
    try:
        # Save each uploaded file to a temporary location with validation
        for upload in files:
            tmp_path, size = await save_upload_to_temp(upload, MAX_FILE_SIZE)
            total_bytes += size
            if total_bytes > MAX_TOTAL_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=f"Total uploaded size exceeds the allowed limit of {MAX_TOTAL_SIZE // (1024 * 1024)} MiB.",
                )
            input_paths.append(tmp_path)

        # Prepare temporary file for the concatenated output
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_out:
            output_path = tmp_out.name

        # Build the pdfunite command
        cmd = ["pdfunite", *input_paths, output_path]

        # Run pdfunite in a thread to avoid blocking the event loop and enforce a timeout
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=PDFUNITE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(
                status_code=500,
                detail="PDF concatenation timed out.",
            )

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"An error occurred while processing the files: {result.stderr.strip()}",
            )

        # Read the concatenated PDF and return it
        with open(output_path, "rb") as f:
            pdf_bytes = f.read()

        return Response(content=pdf_bytes, media_type="application/pdf")

    finally:
        # Clean up all temporary files
        for path in input_paths:
            try:
                os.remove(path)
            except OSError:
                pass
        # Output file may not exist if pdfunite failed
        if "output_path" in locals():
            try:
                os.remove(output_path)
            except OSError:
                pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)