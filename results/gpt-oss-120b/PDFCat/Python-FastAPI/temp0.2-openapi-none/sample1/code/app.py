import os
import subprocess
import tempfile
from typing import List

from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse, Response
import uvicorn

app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Return errors in the required `{ "error": "..."} ` format."""
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.post(
    "/concatenate",
    responses={
        200: {
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
            "description": "Successfully concatenated the PDF files.",
        },
        400: {
            "content": {"application/json": {"schema": {"type": "object", "properties": {"error": {"type": "string"}}}}},
            "description": "Bad Request - Invalid input or missing files.",
        },
        500: {
            "content": {"application/json": {"schema": {"type": "object", "properties": {"error": {"type": "string"}}}}},
            "description": "Internal Server Error - An error occurred while processing the files.",
        },
    },
)
async def concatenate(files: List[UploadFile] = File(...)):
    """
    Accept multiple PDF files and return a single concatenated PDF.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")

    # Verify that all uploaded files are PDFs (basic check)
    for f in files:
        if f.content_type != "application/pdf":
            raise HTTPException(status_code=400, detail="Invalid input or missing files.")

    temp_input_paths = []
    output_path = None

    try:
        # Save each uploaded file to a temporary location
        for upload in files:
            suffix = ".pdf"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                content = await upload.read()
                tmp.write(content)
                temp_input_paths.append(tmp.name)

        # Create a temporary file for the concatenated output
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as out_tmp:
            output_path = out_tmp.name

        # Build the pdfunite command
        cmd = ["pdfunite", *temp_input_paths, output_path]

        # Execute the command
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            # pdfunite failed
            raise HTTPException(
                status_code=500,
                detail="An error occurred while processing the files."
            )

        # Read the concatenated PDF into memory
        with open(output_path, "rb") as f:
            pdf_bytes = f.read()

        # Return the PDF as a response
        return Response(content=pdf_bytes, media_type="application/pdf")

    finally:
        # Clean up all temporary files
        for path in temp_input_paths:
            try:
                os.remove(path)
            except OSError:
                pass
        if output_path:
            try:
                os.remove(output_path)
            except OSError:
                pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)