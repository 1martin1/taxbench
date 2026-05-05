import os
import subprocess
import tempfile

from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from fastapi.responses import PlainTextResponse, JSONResponse
import uvicorn

app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)


def _convert_pdf_to_text(pdf_path: str) -> str:
    """
    Uses the `pdftotext` command‑line tool (provided by poppler-utils) to
    convert a PDF file to plain text.

    Parameters
    ----------
    pdf_path: str
        Path to the PDF file on disk.

    Returns
    -------
    str
        Extracted text.

    Raises
    ------
    RuntimeError
        If `pdftotext` exits with a non‑zero status.
    """
    # `-layout` keeps the original layout, `-enc UTF-8` ensures UTF‑8 output.
    # Using `-` as the output file makes pdftotext write to stdout.
    result = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", pdf_path, "-"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {result.stderr.strip()}")
    return result.stdout


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

    # Basic validation: ensure the uploaded file has a PDF mime type or filename.
    if not (file.filename.lower().endswith(".pdf") or file.content_type == "application/pdf"):
        raise HTTPException(status_code=400, detail="Uploaded file is not a PDF")

    # Write the uploaded file to a temporary location.
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp_path = tmp.name
            # Read the file in chunks to avoid loading huge files entirely into memory.
            while True:
                chunk = await file.read(1024 * 1024)  # 1 MB chunks
                if not chunk:
                    break
                tmp.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to store uploaded file: {str(e)}")
    finally:
        await file.close()

    # Convert the PDF to text.
    try:
        text = _convert_pdf_to_text(tmp_path)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up the temporary file.
        try:
            os.remove(tmp_path)
        except OSError:
            pass

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
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error"},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)