from fastapi import FastAPI, UploadFile, File, HTTPException, status, Response
from pydantic import BaseModel
import tempfile
import subprocess
import os

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
MAX_FILE_SIZE = 10 * 1024 * 1024          # 10 MiB
PDF_MIME_TYPE = "application/pdf"
PDFA2TEXT_CMD = "pdftotext"
TIMEOUT_SECONDS = 15                     # seconds

# ----------------------------------------------------------------------
# Response models
# ----------------------------------------------------------------------
class ErrorResponse(BaseModel):
    error: str

# ----------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------
app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)

@app.post(
    "/convert",
    responses={
        200: {
            "description": "Successfully converted PDF to text",
            "content": {"text/plain": {}},
        },
        400: {
            "model": ErrorResponse,
            "description": "Bad Request - Invalid input or missing file",
        },
        500: {
            "model": ErrorResponse,
            "description": "Internal Server Error - Conversion failed",
        },
    },
)
async def convert(file: UploadFile = File(...)):
    """
    Convert an uploaded PDF file to plain text using the `pdftotext` utility.
    """
    # ------------------------------------------------------------------
    # Validate the uploaded file
    # ------------------------------------------------------------------
    if file.content_type != PDF_MIME_TYPE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Invalid file type. Only PDF files are accepted."},
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": f"File size exceeds limit of {MAX_FILE_SIZE // (1024 * 1024)} MiB."},
        )

    src_path = out_path = None
    try:
        # ------------------------------------------------------------------
        # Write the PDF to a temporary file
        # ------------------------------------------------------------------
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as src_tmp:
            src_path = src_tmp.name
            src_tmp.write(content)

        # ------------------------------------------------------------------
        # Prepare a temporary file for the extracted text
        # ------------------------------------------------------------------
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as out_tmp:
            out_path = out_tmp.name

        # ------------------------------------------------------------------
        # Run `pdftotext` with a timeout and proper error handling
        # ------------------------------------------------------------------
        try:
            result = subprocess.run(
                [PDFA2TEXT_CMD, src_path, out_path],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": "Conversion timed out."},
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": "Conversion utility 'pdftotext' not found on server."},
            )

        if result.returncode != 0:
            err_msg = result.stderr.strip() or "unknown error"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": f"Conversion failed: {err_msg}"},
            )

        # ------------------------------------------------------------------
        # Read the resulting plain‑text content
        # ------------------------------------------------------------------
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                text_content = f.read()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": f"Failed to read conversion output: {str(e)}"},
            )

        return Response(content=text_content, media_type="text/plain")
    finally:
        # ------------------------------------------------------------------
        # Clean up temporary files
        # ------------------------------------------------------------------
        for path in (src_path, out_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)