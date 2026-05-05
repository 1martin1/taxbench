import os
import asyncio
import tempfile
from typing import List

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.responses import Response
import uvicorn

app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
)

# ---- Configuration ----
MAX_FILES = 20                # Maximum number of files per request
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB per file
PDF_HEADER = b"%PDF-"         # Simple PDF magic number
PDFUNITE_TIMEOUT = 30        # seconds


def _is_pdf_header(data: bytes) -> bool:
    """
    Very light validation that the first bytes of a file look like a PDF.
    """
    return data.lstrip().startswith(PDF_HEADER)


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
    summary="Concatenate multiple PDF files",
    description="Accepts multiple PDF files and returns a single concatenated PDF.",
)
async def concatenate(files: List[UploadFile] = File(...)):
    # ---- Input validation ----
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "No files provided."},
        )
    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": f"Too many files. Maximum allowed is {MAX_FILES}."},
        )

    temp_paths: List[str] = []
    output_path: str | None = None

    try:
        # ---- Save uploaded files to temporary files with size and header checks ----
        for upload in files:
            total_read = 0
            chunk_size = 8192

            # Create a temporary file for this upload
            tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            try:
                # Read the first chunk to validate PDF header
                first_chunk = await upload.read(chunk_size)
                if not first_chunk:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={"error": f"File '{upload.filename}' is empty."},
                    )
                if not _is_pdf_header(first_chunk):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={"error": f"File '{upload.filename}' does not appear to be a valid PDF."},
                    )
                tmp_file.write(first_chunk)
                total_read += len(first_chunk)

                # Continue reading the rest of the file, enforcing size limit
                while True:
                    chunk = await upload.read(chunk_size)
                    if not chunk:
                        break
                    total_read += len(chunk)
                    if total_read > MAX_FILE_SIZE:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail={"error": f"File '{upload.filename}' exceeds size limit of {MAX_FILE_SIZE // (1024 * 1024)} MB."},
                        )
                    tmp_file.write(chunk)
                tmp_file.flush()
                temp_paths.append(tmp_file.name)
            finally:
                tmp_file.close()
                # Reset the upload file pointer for safety (not strictly required)
                await upload.seek(0)

        # ---- Create temporary file for the concatenated output ----
        output_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        output_path = output_tmp.name
        output_tmp.close()

        # ---- Run pdfunite asynchronously with timeout ----
        proc = await asyncio.create_subprocess_exec(
            "pdfunite",
            *temp_paths,
            output_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=PDFUNITE_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": "PDF concatenation timed out."},
            )
        if proc.returncode != 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": f"pdfunite failed: {stderr.decode().strip()}"},
            )

        # ---- Read the concatenated PDF and return it ----
        with open(output_path, "rb") as f:
            pdf_bytes = f.read()
        return Response(content=pdf_bytes, media_type="application/pdf")

    except HTTPException:
        # Propagate HTTP errors directly
        raise
    except Exception as exc:
        # Unexpected errors
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "An error occurred while processing the files."},
        ) from exc
    finally:
        # ---- Cleanup temporary files ----
        for path in temp_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
        if output_path:
            try:
                os.unlink(output_path)
            except OSError:
                pass


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000 as required
    uvicorn.run(app, host="0.0.0.0", port=5000)