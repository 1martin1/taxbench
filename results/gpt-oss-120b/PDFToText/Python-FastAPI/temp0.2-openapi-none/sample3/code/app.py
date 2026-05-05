import os
import subprocess
import tempfile
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import PlainTextResponse
import uvicorn

app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)


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
async def convert_pdf_to_text(file: UploadFile = File(...)):
    """
    Convert an uploaded PDF file to plain text using the `pdftotext` utility.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Uploaded file is not a PDF.")

    # Save uploaded file to a temporary location
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            temp_path = tmp.name
            content = await file.read()
            if not content:
                raise HTTPException(status_code=400, detail="Empty file uploaded.")
            tmp.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to store uploaded file: {e}")

    # Run pdftotext, output to stdout
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", temp_path, "-"],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        # Ensure temporary file is removed
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    if result.returncode != 0:
        error_msg = result.stderr.strip() or "pdftotext failed"
        raise HTTPException(status_code=500, detail=error_msg)

    return PlainTextResponse(content=result.stdout, media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)