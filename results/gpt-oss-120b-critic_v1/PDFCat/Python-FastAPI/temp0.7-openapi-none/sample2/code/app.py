import os
import subprocess
import tempfile
import time
from collections import defaultdict
from typing import List

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
    Depends,
)
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
)

# ---------- Configuration ----------
MAX_FILES = 10                     # maximum number of uploaded files per request
MAX_TOTAL_SIZE = 50 * 1024 * 1024  # 50 MB total upload size
MAX_REQUESTS_PER_MINUTE = 20       # simple rate limit per IP
PDF_HEADER = b"%PDF-"

# ---------- Simple in‑memory rate limiter ----------
_rate_store = defaultdict(list)  # ip -> list[timestamps]


def rate_limiter(request: Request):
    ip = request.client.host
    now = time.time()
    window_start = now - 60  # 60‑second window

    timestamps = _rate_store[ip]
    # Remove timestamps outside the window
    while timestamps and timestamps[0] < window_start:
        timestamps.pop(0)

    if len(timestamps) >= MAX_REQUESTS_PER_MINUTE:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again later.",
        )
    timestamps.append(now)


# ---------- Exception handler to match OpenAPI schema ----------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


# ---------- Core processing (blocking) ----------
def _process_files(file_paths: List[str], output_path: str):
    """
    Run pdfunite on the given list of temporary PDF file paths.
    This function runs in a thread pool to avoid blocking the event loop.
    """
    cmd = ["pdfunite", *file_paths, output_path]
    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,  # seconds
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"pdfunite failed: {e.stderr.decode(errors='ignore')}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("pdfunite timed out") from e


# ---------- Endpoint ----------
@app.post(
    "/concatenate",
    responses={
        200: {
            "description": "Successfully concatenated the PDF files.",
            "content": {"application/pdf": {}},
        },
        400: {
            "description": "Bad Request - Invalid input or missing files.",
            "content": {"application/json": {"example": {"error": "Invalid input or missing files."}}},
        },
        429: {
            "description": "Too Many Requests - Rate limit exceeded.",
            "content": {"application/json": {"example": {"error": "Too many requests. Please try again later."}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while processing the files.",
            "content": {"application/json": {"example": {"error": "An error occurred while processing the files."}}},
        },
    },
    dependencies=[Depends(rate_limiter)],
)
async def concatenate(
    request: Request,
    files: List[UploadFile] = File(...),
):
    """
    Accept multiple PDF files and return a single concatenated PDF.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")

    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files. Maximum allowed is {MAX_FILES}.",
        )

    temp_input_paths: List[str] = []
    total_size = 0

    try:
        # Save uploads to temporary files while validating size and PDF header
        for upload in files:
            # Read the first chunk to validate PDF header
            first_chunk = await upload.read(1024)
            if not first_chunk.startswith(PDF_HEADER):
                raise HTTPException(
                    status_code=400,
                    detail="One or more uploaded files are not valid PDF documents.",
                )
            # Create temporary file
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            tmp.write(first_chunk)
            # Continue reading the rest
            while True:
                chunk = await upload.read(1024 * 1024)  # 1 MiB chunks
                if not chunk:
                    break
                tmp.write(chunk)
                total_size += len(chunk)
                if total_size > MAX_TOTAL_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail="Total uploaded size exceeds the allowed limit.",
                    )
            tmp.close()
            temp_input_paths.append(tmp.name)

        # Prepare output temporary file
        output_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        output_path = output_tmp.name
        output_tmp.close()

        # Run pdfunite in a thread pool to avoid blocking the event loop
        await run_in_threadpool(_process_files, temp_input_paths, output_path)

        # Read the resulting PDF into memory (output size is bounded by input limits)
        with open(output_path, "rb") as f:
            concatenated_bytes = f.read()

        return Response(content=concatenated_bytes, media_type="application/pdf")

    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        # Clean up temporary files
        for path in temp_input_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
        try:
            os.unlink(output_path)
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)