import subprocess
import tempfile
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, HTTPException, UploadFile, Response
import uvicorn

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
            "content": {"application/json": {"example": {"error": "Invalid input or missing files."}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while processing the files.",
            "content": {"application/json": {"example": {"error": "An error occurred while processing the files."}}},
        },
    },
)
async def concatenate(files: List[UploadFile] = File(...)):
    """
    Accept multiple PDF files and return a single concatenated PDF.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")

    # Ensure all uploaded files are PDFs (basic check)
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="All uploaded files must be PDFs.")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            input_paths = []
            for idx, upload in enumerate(files):
                # Save each uploaded file to a temporary file
                file_path = tmp_path / f"input_{idx}_{upload.filename}"
                with file_path.open("wb") as out_file:
                    content = await upload.read()
                    out_file.write(content)
                input_paths.append(str(file_path))

            output_path = tmp_path / "concatenated.pdf"

            # Build and run the pdfunite command
            cmd = ["pdfunite", *input_paths, str(output_path)]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                raise RuntimeError(f"pdfunite failed: {result.stderr}")

            # Read the concatenated PDF
            with output_path.open("rb") as f:
                pdf_bytes = f.read()

            return Response(content=pdf_bytes, media_type="application/pdf")

    except HTTPException:
        # Re‑raise HTTP errors unchanged
        raise
    except Exception as exc:
        # Log the exception if needed (omitted here) and return a 500 error
        raise HTTPException(status_code=500, detail="An error occurred while processing the files.") from exc


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)