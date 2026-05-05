import os
import shutil
import subprocess
import tempfile
from typing import List, Optional

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
import uvicorn


MAX_FILES = 20
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MiB per file
MAX_TOTAL_SIZE_BYTES = 100 * 1024 * 1024  # 100 MiB total
MAX_OUTPUT_SIZE_BYTES = 150 * 1024 * 1024  # 150 MiB
PDF_HEADER = b"%PDF-"
PDFUNITE_TIMEOUT_SECONDS = 30


app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
)


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(400, "Invalid input or missing files.")


def _is_pdf_header(header: bytes) -> bool:
    return header.startswith(PDF_HEADER)


@app.post(
    "/concatenate",
    summary="Concatenate multiple PDF files",
    description="Accepts multiple PDF files and returns a single concatenated PDF.",
    responses={
        200: {
            "description": "Successfully concatenated the PDF files.",
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
        },
        400: {
            "description": "Bad Request - Invalid input or missing files.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"error": {"type": "string", "example": "Invalid input or missing files."}},
                    }
                }
            },
        },
        500: {
            "description": "Internal Server Error - An error occurred while processing the files.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "error": {"type": "string", "example": "An error occurred while processing the files."}
                        },
                    }
                }
            },
        },
    },
)
async def concatenate_pdfs(files: Optional[List[UploadFile]] = File(None)) -> Response:
    if not files:
        return error_response(400, "Invalid input or missing files.")

    if len(files) > MAX_FILES:
        return error_response(400, "Invalid input or missing files.")

    if shutil.which("pdfunite") is None:
        return error_response(500, "An error occurred while processing the files.")

    total_size = 0

    with tempfile.TemporaryDirectory() as temp_dir:
        input_paths: List[str] = []

        try:
            for index, upload in enumerate(files):
                if upload is None or not upload.filename:
                    return error_response(400, "Invalid input or missing files.")

                content_type = (upload.content_type or "").lower().strip()
                if content_type != "application/pdf":
                    return error_response(400, "Invalid input or missing files.")

                input_path = os.path.join(temp_dir, f"input_{index}.pdf")
                bytes_written = 0
                first_chunk = True

                with open(input_path, "wb") as destination:
                    while True:
                        chunk = await upload.read(1024 * 1024)
                        if not chunk:
                            break

                        if first_chunk:
                            first_chunk = False
                            if not _is_pdf_header(chunk):
                                return error_response(400, "Invalid input or missing files.")

                        bytes_written += len(chunk)
                        total_size += len(chunk)

                        if bytes_written > MAX_FILE_SIZE_BYTES or total_size > MAX_TOTAL_SIZE_BYTES:
                            return error_response(400, "Invalid input or missing files.")

                        destination.write(chunk)

                if bytes_written == 0:
                    return error_response(400, "Invalid input or missing files.")

                input_paths.append(input_path)

            if not input_paths:
                return error_response(400, "Invalid input or missing files.")

            output_path = os.path.join(temp_dir, "concatenated.pdf")
            command = ["pdfunite", *input_paths, output_path]

            try:
                result = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=PDFUNITE_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                return error_response(500, "An error occurred while processing the files.")

            if result.returncode != 0 or not os.path.exists(output_path):
                stderr_output = result.stderr or b""
                if b"pdf" in stderr_output.lower() or b"syntax" in stderr_output.lower():
                    return error_response(400, "Invalid input or missing files.")
                return error_response(500, "An error occurred while processing the files.")

            output_size = os.path.getsize(output_path)
            if output_size <= 0 or output_size > MAX_OUTPUT_SIZE_BYTES:
                return error_response(500, "An error occurred while processing the files.")

            with open(output_path, "rb") as output_file:
                output_bytes = output_file.read()

            if not _is_pdf_header(output_bytes[:5]):
                return error_response(500, "An error occurred while processing the files.")

            return Response(
                content=output_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": 'attachment; filename="concatenated.pdf"'},
            )

        except Exception:
            return error_response(500, "An error occurred while processing the files.")
        finally:
            for upload in files:
                try:
                    await upload.close()
                except Exception:
                    pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)