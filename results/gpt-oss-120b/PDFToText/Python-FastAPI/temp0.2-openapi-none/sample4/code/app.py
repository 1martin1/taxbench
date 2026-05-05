import os
import subprocess
import tempfile

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile, Response

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
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        400: {"description": "Bad Request - Invalid input or missing file"},
        500: {"description": "Internal Server Error - Conversion failed"},
    },
)
async def convert(file: UploadFile = File(...)):
    """
    Convert an uploaded PDF file to plain text using the `pdftotext` utility.
    """
    if not file:
        raise HTTPException(status_code=400, detail="No file provided")

    # Ensure the uploaded file is a PDF (optional, but helpful)
    if file.content_type != "application/pdf" and not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Uploaded file is not a PDF")

    # Save uploaded PDF to a temporary file
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_input:
            input_path = tmp_input.name
            content = await file.read()
            tmp_input.write(content)

        # Prepare temporary file for the text output
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp_output:
            output_path = tmp_output.name

        # Run pdftotext
        result = subprocess.run(
            ["pdftotext", input_path, output_path],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Conversion failed: {result.stderr.strip() or 'Unknown error'}",
            )

        # Read the resulting text
        with open(output_path, "r", encoding="utf-8") as f:
            text_content = f.read()

        return Response(content=text_content, media_type="text/plain")
    finally:
        # Clean up temporary files
        for path in [locals().get("input_path"), locals().get("output_path")]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)