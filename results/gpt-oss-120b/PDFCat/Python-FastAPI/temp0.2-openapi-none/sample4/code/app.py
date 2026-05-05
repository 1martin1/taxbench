import os
import shutil
import subprocess
import tempfile
from typing import List

from fastapi import FastAPI, File, HTTPException, UploadFile, Response

app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
)


@app.post(
    "/concatenate",
    responses={
        200: {
            "description": "Successfully concatenated the PDF files.",
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
        },
        400: {
            "description": "Bad Request - Invalid input or missing files.",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"error": {"type": "string"}}}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while processing the files.",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"error": {"type": "string"}}}}},
        },
    },
)
async def concatenate(files: List[UploadFile] = File(...)):
    """
    Accept multiple PDF files and return a single concatenated PDF.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")

    # Ensure all uploaded files are PDFs (basic check by filename)
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="All uploaded files must be PDF documents.")

    temp_dir = tempfile.mkdtemp()
    input_paths = []
    output_path = os.path.join(temp_dir, "concatenated.pdf")

    try:
        # Save uploaded files to temporary files
        for idx, upload in enumerate(files):
            temp_path = os.path.join(temp_dir, f"input_{idx}.pdf")
            with open(temp_path, "wb") as out_file:
                content = await upload.read()
                out_file.write(content)
            input_paths.append(temp_path)

        # Build and run pdfunite command
        cmd = ["pdfunite", *input_paths, output_path]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"An error occurred while processing the files: {result.stderr.strip()}",
            )

        # Read the concatenated PDF
        with open(output_path, "rb") as f:
            pdf_bytes = f.read()

        return Response(content=pdf_bytes, media_type="application/pdf")

    finally:
        # Cleanup temporary files and directory
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)