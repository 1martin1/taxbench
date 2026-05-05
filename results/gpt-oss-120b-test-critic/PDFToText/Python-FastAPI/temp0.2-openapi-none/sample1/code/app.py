import os
import subprocess
import tempfile
import logging

from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from fastapi.responses import PlainTextResponse, JSONResponse
import uvicorn

# Configure basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)


def _convert_pdf_to_text(pdf_path: str) -> str:
    """
    Convert a PDF file to plain text using the `pdftotext` utility.

    Raises:
        RuntimeError: If the conversion tool is missing or the conversion fails.
    """
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", pdf_path, "-"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except FileNotFoundError:
        logger.error("pdftotext binary not found.")
        raise RuntimeError("Conversion tool is unavailable.")
    except subprocess.CalledProcessError as e:
        logger.error("pdftotext failed: %s", e.stderr.strip())
        raise RuntimeError("Failed to convert PDF to text.")


@app.post(
    "/convert",
    response_class=PlainTextResponse,
    responses={
        200: {
            "description": "Successfully converted PDF to text",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        400: {"description": "Bad Request - Invalid input or missing file"},
        500: {"description": "Internal Server Error - Conversion failed"},
    },
)
async def convert(file: UploadFile = File(...)):
    """
    Convert a PDF file to plain text.
    """
    if not file:
        raise HTTPException(status_code=400, detail="No file provided")

    # Basic MIME type / extension validation
    if not (file.filename.lower().endswith(".pdf") or file.content_type == "application/pdf"):
        raise HTTPException(status_code=400, detail="Uploaded file is not a PDF")

    # Reject obviously malicious filenames
    if any(char in file.filename for char in (";", "&", "|", "`", "$", ">", "<")):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Verify PDF header (%PDF-)
    try:
        first_chunk = await file.read(1024)  # read up to 1 KiB for header check
        if not first_chunk.startswith(b"%PDF-"):
            raise HTTPException(status_code=400, detail="File content is not a valid PDF")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error while reading uploaded file.")
        raise HTTPException(status_code=500, detail="Failed to read uploaded file")

    # Write the uploaded file to a temporary location
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp_path = tmp.name
            tmp.write(first_chunk)
            while True:
                chunk = await file.read(1024 * 1024)  # 1 MiB chunks
                if not chunk:
                    break
                tmp.write(chunk)
    except Exception as e:
        logger.exception("Failed to store uploaded file.")
        raise HTTPException(status_code=500, detail="Failed to store uploaded file")
    finally:
        await file.close()

    # Convert the PDF to text
    try:
        text = _convert_pdf_to_text(tmp_path)
    except RuntimeError:
        raise HTTPException(status_code=500, detail="Failed to convert PDF to text")
    finally:
        # Clean up the temporary file
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                logger.warning("Could not delete temporary file: %s", tmp_path)

    return PlainTextResponse(content=text, media_type="text/plain")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Return errors in the format defined by the OpenAPI schema.
    """
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """
    Catch‑all handler for unexpected errors.
    """
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error"},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)