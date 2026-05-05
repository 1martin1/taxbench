import os
import subprocess
import tempfile
from typing import Optional

import asyncio
from fastapi import FastAPI, File, HTTPException, UploadFile, Response
import uvicorn

app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)

# Maximum allowed uploaded PDF size (10 MiB)
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
# Timeout for the pdftotext utility (seconds)
PDftotext_TIMEOUT = 30


def _run_pdftotext(pdf_path: str) -> str:
    """
    Execute `pdftotext` on the given PDF file and return the extracted text.

    This function is intended to be called inside a thread pool to avoid blocking
    the event loop.
    """
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True,
            text=True,
            timeout=PDftotext_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Conversion timed out")
    except Exception as exc:
        raise RuntimeError(f"Conversion error: {exc}") from exc

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "pdftotext failed")
    return result.stdout


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
    if not file:
        raise HTTPException(status_code=400, detail="File not provided")

    # Basic validation of file type
    if file.content_type != "application/pdf" and not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Uploaded file is not a PDF")

    tmp_path: Optional[str] = None
    total_read = 0

    try:
        # Create a temporary file to store the uploaded PDF
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
            tmp_path = tmp_file.name
            # Read the upload in chunks to enforce size limit and avoid loading whole file into memory
            while True:
                chunk = await file.read(1024 * 1024)  # 1 MiB per chunk
                if not chunk:
                    break
                total_read += len(chunk)
                if total_read > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail="Uploaded file exceeds the maximum allowed size of 10 MiB",
                    )
                tmp_file.write(chunk)
    except HTTPException:
        # Propagate HTTP errors directly
        raise
    except Exception:
        # Hide internal details from the client
        raise HTTPException(status_code=500, detail="Failed to store uploaded file")
    finally:
        await file.close()

    # Run the conversion in a thread pool to keep the endpoint non‑blocking
    try:
        text_content = await asyncio.to_thread(_run_pdftotext, tmp_path)
    except RuntimeError as exc:
        # Log the actual error internally if needed (omitted here for brevity)
        raise HTTPException(status_code=500, detail="Failed to convert PDF to text")
    finally:
        # Ensure the temporary file is removed regardless of success or failure
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass  # If deletion fails we silently ignore to avoid masking the original response

    return Response(content=text_content, media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)