import os
import subprocess
import tempfile
from typing import List

from fastapi import FastAPI, File, UploadFile, Response
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
)

# Security limits
MAX_FILES = 10                     # maximum number of files per request
MAX_TOTAL_SIZE = 50 * 1024 * 1024  # 50 MB total upload size
PDF_HEADER = b"%PDF-"
SUBPROCESS_TIMEOUT = 30           # seconds


@app.post(
    "/concatenate",
    responses={
        200: {
            "description": "Successfully concatenated the PDF files.",
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
        },
        400: {
            "description": "Bad Request - Invalid input or missing files.",
            "content": {
                "application/json": {
                    "example": {"error": "Invalid input or missing files."}
                }
            },
        },
        500: {
            "description": "Internal Server Error - An error occurred while processing the files.",
            "content": {
                "application/json": {
                    "example": {"error": "An error occurred while processing the files."}
                }
            },
        },
    },
)
async def concatenate(files: List[UploadFile] = File(...)):
    """
    Accept multiple PDF files and return a single concatenated PDF.
    """
    # Basic request validation
    if not files:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid input or missing files."},
        )

    if len(files) > MAX_FILES:
        return JSONResponse(
            status_code=400,
            content={"error": f"Too many files. Maximum allowed is {MAX_FILES}."},
        )

    total_size = 0
    temp_input_paths: List[str] = []
    output_path: str | None = None

    try:
        # Save each uploaded file to a temporary file after validation
        for upload in files:
            content = await upload.read()
            file_size = len(content)

            # Enforce total size limit
            total_size += file_size
            if total_size > MAX_TOTAL_SIZE:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Total uploaded size exceeds {MAX_TOTAL_SIZE // (1024 * 1024)} MB limit."},
                )

            # Verify basic PDF structure
            if not content.startswith(PDF_HEADER):
                return JSONResponse(
                    status_code=400,
                    content={"error": "One or more files are not valid PDF documents."},
                )

            # Write to a temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(content)
                tmp.flush()
                temp_input_paths.append(tmp.name)

        # Prepare temporary file for the concatenated output
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_out:
            output_path = tmp_out.name

        # Build and execute the pdfunite command
        cmd = ["pdfunite", *temp_input_paths, output_path]

        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=SUBPROCESS_TIMEOUT,
            )
        except FileNotFoundError as e:
            # pdfunite binary is missing
            return JSONResponse(
                status_code=500,
                content={"error": "pdfunite binary not found on server."},
            )
        except subprocess.CalledProcessError as e:
            # pdfunite reported an error
            return JSONResponse(
                status_code=500,
                content={"error": "An error occurred while processing the files."},
            )
        except subprocess.TimeoutExpired as e:
            # pdfunite hung or took too long
            return JSONResponse(
                status_code=500,
                content={"error": "PDF processing timed out."},
            )

        # Read the concatenated PDF into memory
        with open(output_path, "rb") as f:
            pdf_bytes = f.read()

        return Response(content=pdf_bytes, media_type="application/pdf")

    except Exception:
        # Catch-all for unexpected errors
        return JSONResponse(
            status_code=500,
            content={"error": "An error occurred while processing the files."},
        )
    finally:
        # Clean up temporary input files
        for path in temp_input_paths:
            try:
                os.remove(path)
            except OSError:
                pass
        # Clean up temporary output file
        if output_path:
            try:
                os.remove(output_path)
            except OSError:
                pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)