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


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    """Return errors in the format required by the OpenAPI spec."""
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


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

    # Verify that each uploaded file is a PDF (basic check)
    for file in files:
        if file.content_type != "application/pdf":
            raise HTTPException(status_code=400, detail="Invalid input or missing files.")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_paths = []
            # Save uploaded files to temporary files
            for idx, upload in enumerate(files):
                input_path = os.path.join(tmpdir, f"file_{idx}.pdf")
                with open(input_path, "wb") as f:
                    content = await upload.read()
                    f.write(content)
                input_paths.append(input_path)

            output_path = os.path.join(tmpdir, "concatenated.pdf")

            # Build and run the pdfunite command
            cmd = ["pdfunite"] + input_paths + [output_path]
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                # pdfunite failed
                raise HTTPException(
                    status_code=500,
                    detail="An error occurred while processing the files.",
                )

            # Read the resulting PDF into memory
            with open(output_path, "rb") as f:
                merged_pdf = f.read()

        return Response(content=merged_pdf, media_type="application/pdf")
    except HTTPException:
        # ReвЂ‘raise to be handled by the custom exception handler
        raise
    except Exception as e:
        # Unexpected errors
        raise HTTPException(
            status_code=500,
            detail="An error occurred while processing the files.",
        ) from e


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)