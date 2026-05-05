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


APP_TITLE = "PDF Concatenation Service"
APP_DESCRIPTION = "A service that concatenates multiple PDF files into a single PDF."
APP_VERSION = "1.0.0"

ERROR_INVALID_INPUT = "Invalid input or missing files."
ERROR_PROCESSING = "An error occurred while processing the files."

MAX_FILES = 50
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_SIZE_BYTES = 100 * 1024 * 1024
MAX_OUTPUT_SIZE_BYTES = 150 * 1024 * 1024
PDFUNITE_TIMEOUT_SECONDS = 30
PDF_HEADER = b"%PDF-"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=APP_TITLE,
    description=APP_DESCRIPTION,
    version=APP_VERSION,
)


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return error_response(400, ERROR_INVALID_INPUT)


def is_pdf_content(content: bytes) -> bool:
    return content.startswith(PDF_HEADER)


def validate_upload_metadata(upload: UploadFile) -> bool:
    filename = upload.filename or ""
    content_type = (upload.content_type or "").lower()

    if not filename.lower().endswith(".pdf"):
        return False

    if content_type and content_type not in {"application/pdf", "application/octet-stream"}:
        return False

    return True


async def save_upload_with_limits(
    upload: UploadFile,
    destination_path: str,
    total_size_so_far: int,
) -> Optional[int]:
    written = 0

    with open(destination_path, "wb") as destination:
        first_chunk_checked = False

        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break

            if not first_chunk_checked:
                first_chunk_checked = True
                if not is_pdf_content(chunk):
                    return None

            written += len(chunk)
            if written > MAX_FILE_SIZE_BYTES:
                return None

            if total_size_so_far + written > MAX_TOTAL_SIZE_BYTES:
                return None

            destination.write(chunk)

    if written == 0:
        return None

    return written


@app.post(
    "/concatenate",
    summary="Concatenate multiple PDF files",
    description="Accepts multiple PDF files and returns a single concatenated PDF.",
    responses={
        200: {
            "description": "Successfully concatenated the PDF files.",
            "content": {
                "application/pdf": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
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
async def concatenate(files: Optional[List[UploadFile]] = File(None)) -> Response:
    if not files:
        return error_response(400, ERROR_INVALID_INPUT)

    if len(files) > MAX_FILES:
        return error_response(400, ERROR_INVALID_INPUT)

    if shutil.which("pdfunite") is None:
        logger.error("pdfunite binary not found in PATH")
        return error_response(500, ERROR_PROCESSING)

    temp_dir = tempfile.mkdtemp(prefix="pdf_concat_")
    input_paths: List[str] = []
    output_path = os.path.join(temp_dir, "concatenated.pdf")
    total_size = 0

    try:
        for index, upload in enumerate(files):
            if not validate_upload_metadata(upload):
                return error_response(400, ERROR_INVALID_INPUT)

            file_path = os.path.join(temp_dir, f"input_{index}.pdf")
            written = await save_upload_with_limits(upload, file_path, total_size)

            if written is None:
                return error_response(400, ERROR_INVALID_INPUT)

            total_size += written
            input_paths.append(file_path)

        if not input_paths:
            return error_response(400, ERROR_INVALID_INPUT)

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
            logger.exception("pdfunite timed out")
            return error_response(500, ERROR_PROCESSING)
        except OSError:
            logger.exception("Failed to execute pdfunite")
            return error_response(500, ERROR_PROCESSING)

        if result.returncode != 0 or not os.path.exists(output_path):
            logger.error(
                "pdfunite failed with return code %s and stderr: %s",
                result.returncode,
                result.stderr.decode("utf-8", errors="ignore"),
            )
            return error_response(500, ERROR_PROCESSING)

        output_size = os.path.getsize(output_path)
        if output_size <= 0 or output_size > MAX_OUTPUT_SIZE_BYTES:
            logger.error("Output PDF size is invalid: %s bytes", output_size)
            return error_response(500, ERROR_PROCESSING)

        with open(output_path, "rb") as f:
            merged_pdf = f.read()

        if not is_pdf_content(merged_pdf):
            logger.error("Merged output is not a valid PDF header")
            return error_response(500, ERROR_PROCESSING)

        return Response(
            content=merged_pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="concatenated.pdf"'},
        )

    except Exception:
        logger.exception("Unhandled error while processing concatenate request")
        return error_response(500, ERROR_PROCESSING)
    finally:
        for upload in files or []:
            with suppress(Exception):
                await upload.close()
        with suppress(Exception):
            shutil.rmtree(temp_dir)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)