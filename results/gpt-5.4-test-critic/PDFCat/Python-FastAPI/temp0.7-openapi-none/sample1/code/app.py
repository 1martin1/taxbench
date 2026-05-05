import logging
import os
import shutil
import subprocess
import tempfile
from contextlib import suppress
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response


MAX_FILES = 20
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_SIZE_BYTES = 50 * 1024 * 1024
READ_CHUNK_SIZE = 1024 * 1024
PDF_MAGIC = b"%PDF-"
ALLOWED_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
)


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response("Invalid input or missing files.", 400)


def is_valid_pdf_header(header: bytes) -> bool:
    return header.startswith(PDF_MAGIC)


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
                        "properties": {
                            "error": {
                                "type": "string",
                                "example": "Invalid input or missing files.",
                            }
                        },
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
                            "error": {
                                "type": "string",
                                "example": "An error occurred while processing the files.",
                            }
                        },
                    }
                }
            },
        },
    },
)
async def concatenate(files: Optional[List[UploadFile]] = File(None)):
    if not files:
        return error_response("Invalid input or missing files.", 400)

    if len(files) > MAX_FILES:
        return error_response("Invalid input or missing files.", 400)

    if shutil.which("pdfunite") is None:
        logger.error("pdfunite executable not found")
        return error_response("An error occurred while processing the files.", 500)

    temp_paths = []
    temp_dir = tempfile.mkdtemp(prefix="pdf_concat_")
    total_size = 0

    try:
        for index, upload in enumerate(files):
            filename = upload.filename or f"file_{index + 1}.pdf"

            if not filename.lower().endswith(".pdf"):
                return error_response("Invalid input or missing files.", 400)

            if upload.content_type not in ALLOWED_CONTENT_TYPES:
                return error_response("Invalid input or missing files.", 400)

            input_path = os.path.join(temp_dir, f"input_{index + 1}.pdf")
            file_size = 0
            first_chunk = True
            saw_data = False

            with open(input_path, "wb") as output_file:
                while True:
                    chunk = await upload.read(READ_CHUNK_SIZE)
                    if not chunk:
                        break

                    if first_chunk:
                        first_chunk = False
                        if not is_valid_pdf_header(chunk):
                            return error_response("Invalid input or missing files.", 400)

                    saw_data = True
                    chunk_len = len(chunk)
                    file_size += chunk_len
                    total_size += chunk_len

                    if file_size > MAX_FILE_SIZE_BYTES or total_size > MAX_TOTAL_SIZE_BYTES:
                        return error_response("Invalid input or missing files.", 400)

                    output_file.write(chunk)

            if not saw_data:
                return error_response("Invalid input or missing files.", 400)

            temp_paths.append(input_path)

        if not temp_paths:
            return error_response("Invalid input or missing files.", 400)

        output_path = os.path.join(temp_dir, "concatenated.pdf")
        command = ["pdfunite", *temp_paths, output_path]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
        )

        if result.returncode != 0 or not os.path.exists(output_path):
            logger.error(
                "pdfunite failed with return code %s: %s",
                result.returncode,
                result.stderr.decode("utf-8", errors="replace"),
            )
            return error_response("An error occurred while processing the files.", 500)

        with open(output_path, "rb") as f:
            merged_pdf = f.read()

        if not merged_pdf or not is_valid_pdf_header(merged_pdf[:8]):
            logger.error("Generated output is not a valid PDF")
            return error_response("An error occurred while processing the files.", 500)

        return Response(
            content=merged_pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="concatenated.pdf"'},
        )

    except subprocess.TimeoutExpired:
        logger.exception("pdfunite timed out")
        return error_response("An error occurred while processing the files.", 500)
    except OSError:
        logger.exception("Filesystem or process execution error")
        return error_response("An error occurred while processing the files.", 500)
    finally:
        for upload in files:
            with suppress(Exception):
                await upload.close()
        with suppress(Exception):
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)