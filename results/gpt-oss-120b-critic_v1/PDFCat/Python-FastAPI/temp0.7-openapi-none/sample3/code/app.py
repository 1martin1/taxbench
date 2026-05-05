import os
import shutil
import subprocess
import tempfile
from typing import List

import anyio
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
)

# ----- Configuration -----
MAX_FILES = 20                     # Maximum number of files per request
MAX_FILE_SIZE = 10 * 1024 * 1024   # 10 MB per file
MAX_TOTAL_SIZE = 50 * 1024 * 1024  # 50 MB total per request
PDFUNITE_TIMEOUT = 30             # Seconds
# -------------------------

def _ensure_pdfunite_available() -> None:
    """Verify that the pdfunite binary is available on the system."""
    if shutil.which("pdfunite") is None:
        raise RuntimeError("pdfunite binary is not installed or not in PATH.")

@app.on_event("startup")
async def startup_event():
    try:
        _ensure_pdfunite_available()
    except RuntimeError as exc:
        # Fail fast – the service cannot work without pdfunite.
        raise RuntimeError(f"Startup failed: {exc}") from exc

def _validate_pdf_header(initial_bytes: bytes) -> bool:
    """Very small sanity check – PDF files start with %PDF."""
    return initial_bytes.startswith(b"%PDF")

async def _save_uploaded_file(
    upload: UploadFile,
    destination_path: str,
    per_file_limit: int,
) -> int:
    """
    Stream the uploaded file to disk while enforcing a size limit.
    Returns the total number of bytes written.
    """
    total_written = 0

    # Read the first few bytes to validate PDF header.
    header = await upload.read(4)
    if not _validate_pdf_header(header):
        raise HTTPException(
            status_code=400,
            detail="One or more uploaded files are not valid PDFs.",
        )
    total_written += len(header)

    # Write header and continue streaming the rest.
    with open(destination_path, "wb") as out_file:
        out_file.write(header)
        while True:
            chunk = await upload.read(1024 * 1024)  # 1 MB chunks
            if not chunk:
                break
            total_written += len(chunk)
            if total_written > per_file_limit:
                raise HTTPException(
                    status_code=400,
                    detail=f"File size exceeds per-file limit of {per_file_limit // (1024 * 1024)} MB.",
                )
            out_file.write(chunk)

    return total_written

async def _run_pdfunite(input_paths: List[str], output_path: str) -> None:
    """
    Execute pdfunite in a thread pool to avoid blocking the event loop.
    A timeout is applied to guard against hangs.
    """
    def _exec():
        result = subprocess.run(
            ["pdfunite", *input_paths, output_path],
            capture_output=True,
            text=True,
            timeout=PDFUNITE_TIMEOUT,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"pdfunite failed: {result.stderr.strip()}")

    await anyio.to_thread.run_sync(_exec)

@app.post(
    "/concatenate",
    responses={
        200: {"content": {"application/pdf": {}}},
        400: {"content": {"application/json": {}}},
        500: {"content": {"application/json": {}}},
    },
)
async def concatenate(files: List[UploadFile] = File(...)):
    """
    Accept multiple PDF files and return a single concatenated PDF.
    """
    # ----- Input validation -----
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")

    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Number of files exceeds the maximum allowed ({MAX_FILES}).",
        )
    # ----------------------------

    with tempfile.TemporaryDirectory() as tmpdir:
        input_paths: List[str] = []
        total_size = 0

        try:
            # Save each uploaded file to a temporary location while enforcing limits.
            for idx, upload in enumerate(files):
                tmp_path = os.path.join(tmpdir, f"input_{idx}.pdf")
                file_size = await _save_uploaded_file(
                    upload,
                    tmp_path,
                    per_file_limit=MAX_FILE_SIZE,
                )
                total_size += file_size
                if total_size > MAX_TOTAL_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Total uploaded size exceeds the limit of {MAX_TOTAL_SIZE // (1024 * 1024)} MB.",
                    )
                input_paths.append(tmp_path)

            # Path for the concatenated output.
            output_path = os.path.join(tmpdir, "output.pdf")

            # Run pdfunite without blocking the event loop.
            await _run_pdfunite(input_paths, output_path)

            # Read the concatenated PDF into memory (size is limited by input limits).
            with open(output_path, "rb") as f:
                pdf_bytes = f.read()

            return Response(content=pdf_bytes, media_type="application/pdf")

        except HTTPException:
            # Propagate HTTP errors directly.
            raise
        except FileNotFoundError as exc:
            # pdfunite binary missing – this should have been caught at startup,
            # but we guard against it here as well.
            raise HTTPException(
                status_code=500,
                detail="pdfunite binary is not available on the server.",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(
                status_code=500,
                detail="PDF concatenation timed out.",
            ) from exc
        except RuntimeError as exc:
            # Generic processing errors.
            raise HTTPException(
                status_code=500,
                detail="An error occurred while processing the files.",
            ) from exc
        except Exception as exc:
            # Unexpected errors – do not expose internal details.
            raise HTTPException(
                status_code=500,
                detail="An internal server error occurred.",
            ) from exc

if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000)