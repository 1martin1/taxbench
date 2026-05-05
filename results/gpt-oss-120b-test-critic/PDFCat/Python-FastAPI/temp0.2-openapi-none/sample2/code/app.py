import os
import subprocess
import tempfile
from typing import List

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
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
            "content": {"application/json": {"schema": {"type": "object", "properties": {"error": {"type": "string", "example": "Invalid input or missing files."}}}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while processing the files.",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"error": {"type": "string", "example": "An error occurred while processing the files."}}}}},
        },
    },
)
async def concatenate(files: List[UploadFile] = File(...)):
    """
    Accept multiple PDF files and return a single concatenated PDF.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")

    # Verify that at least one file is provided
    if len(files) == 0:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")

    input_paths = []
    temp_files = []  # keep references to NamedTemporaryFile objects for cleanup

    try:
        # Save each uploaded file to a temporary location
        for upload in files:
            # Ensure the uploaded file is a PDF (basic check)
            if not upload.filename.lower().endswith(".pdf"):
                raise HTTPException(status_code=400, detail="All uploaded files must be PDFs.")
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            temp_files.append(tmp)  # store for later removal
            with tmp as f:
                content = await upload.read()
                f.write(content)
                f.flush()
                input_paths.append(f.name)

        # Create a temporary file for the output PDF
        output_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        output_path = output_tmp.name
        output_tmp.close()  # pdfunite will write to this file

        # Build the pdfunite command
        cmd = ["pdfunite", *input_paths, output_path]

        # Execute the command
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            # pdfunite failed
            raise RuntimeError(f"pdfunite error: {result.stderr.strip()}")

        # Read the concatenated PDF into memory
        with open(output_path, "rb") as f:
            pdf_bytes = f.read()

        # Return the PDF as a response
        return Response(content=pdf_bytes, media_type="application/pdf")

    except HTTPException:
        # Re-raise HTTP exceptions unchanged
        raise
    except Exception as e:
        # Log the exception if needed (omitted for brevity)
        raise HTTPException(status_code=500, detail="An error occurred while processing the files.") from e
    finally:
        # Cleanup all temporary files
        for path in input_paths:
            try:
                os.remove(path)
            except OSError:
                pass
        for tmp in temp_files:
            try:
                os.remove(tmp.name)
            except OSError:
                pass
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)