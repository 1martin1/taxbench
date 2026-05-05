import logging
import os
import shutil
import subprocess
import tempfile
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
import uvicorn


PDF_CONTENT_TYPE = "application/pdf"
GENERIC_BAD_REQUEST_MESSAGE = "Invalid input or missing files."
GENERIC_SERVER_ERROR_MESSAGE = "An error occurred while processing the files."

MAX_FILES = 20
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_INPUT_BYTES = 50 * 1024 * 1024
MAX_OUTPUT_SIZE_BYTES = 100 * 1024 * 1024
SUBPROCESS_TIMEOUT_SECONDS = 30
PDF_MAGIC = b"%PDF-"

logger = logging.getLogger("pdf_concat_service")
logging.basicConfig(level=logging.INFO)


def create_error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def is_pdf_bytes(file_bytes: bytes) -> bool:
    return bool(file_bytes) and file_bytes.startswith(PDF_MAGIC)


async def read_upload_with_limit(upload: UploadFile, max_bytes: int) -> Optional[bytes]:
    chunks = []
    total = 0

    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            return None
        chunks.append(chunk)

    if total == 0:
        return b""

    return b"".join(chunks)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if shutil.which("pdfunite") is None:
        logger.error("pdfunite CLI is not installed or not available in PATH.")
    yield


app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, __: RequestValidationError):
    return create_error_response(400, GENERIC_BAD_REQUEST_MESSAGE)


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
async def concatenate_pdfs(files: Optional[List[UploadFile]] = File(None)):
    if not files:
        return create_error_response(400, GENERIC_BAD_REQUEST_MESSAGE)

    if len(files) > MAX_FILES:
        return create_error_response(400, GENERIC_BAD_REQUEST_MESSAGE)

    if shutil.which("pdfunite") is None:
        logger.error("pdfunite CLI is not installed or not available in PATH.")
        for upload in files:
            try:
                await upload.close()
            except Exception:
                logger.warning("Failed to close upload during startup dependency check.", exc_info=True)
        return create_error_response(500, GENERIC_SERVER_ERROR_MESSAGE)

    total_input_bytes = 0

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_paths = []

            for index, upload in enumerate(files):
                if upload is None:
                    return create_error_response(400, GENERIC_BAD_REQUEST_MESSAGE)

                file_bytes = await read_upload_with_limit(upload, MAX_FILE_SIZE_BYTES)
                if file_bytes is None:
                    return create_error_response(400, GENERIC_BAD_REQUEST_MESSAGE)

                if not file_bytes or not is_pdf_bytes(file_bytes):
                    return create_error_response(400, GENERIC_BAD_REQUEST_MESSAGE)

                total_input_bytes += len(file_bytes)
                if total_input_bytes > MAX_TOTAL_INPUT_BYTES:
                    return create_error_response(400, GENERIC_BAD_REQUEST_MESSAGE)

                input_path = os.path.join(temp_dir, f"input_{index}.pdf")
                with open(input_path, "wb") as f:
                    f.write(file_bytes)

                input_paths.append(input_path)

            output_path = os.path.join(temp_dir, "concatenated.pdf")
            command = ["pdfunite", *input_paths, output_path]

            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=SUBPROCESS_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                logger.error("pdfunite timed out while processing request.")
                return create_error_response(500, GENERIC_SERVER_ERROR_MESSAGE)
            except OSError:
                logger.exception("Failed to execute pdfunite.")
                return create_error_response(500, GENERIC_SERVER_ERROR_MESSAGE)

            if result.returncode != 0 or not os.path.exists(output_path):
                logger.error(
                    "pdfunite failed with return code %s. stderr=%s",
                    result.returncode,
                    result.stderr.strip() if result.stderr else "",
                )
                return create_error_response(500, GENERIC_SERVER_ERROR_MESSAGE)

            try:
                output_size = os.path.getsize(output_path)
            except OSError:
                logger.exception("Failed to stat concatenated output file.")
                return create_error_response(500, GENERIC_SERVER_ERROR_MESSAGE)

            if output_size <= 0 or output_size > MAX_OUTPUT_SIZE_BYTES:
                return create_error_response(500, GENERIC_SERVER_ERROR_MESSAGE)

            with open(output_path, "rb") as f:
                output_bytes = f.read()

            if not is_pdf_bytes(output_bytes):
                logger.error("Generated output is not a valid PDF header.")
                return create_error_response(500, GENERIC_SERVER_ERROR_MESSAGE)

            return Response(
                content=output_bytes,
                media_type=PDF_CONTENT_TYPE,
                headers={
                    "Content-Disposition": 'attachment; filename="concatenated.pdf"'
                },
            )
    except Exception:
        logger.exception("Unexpected error while processing PDF concatenation request.")
        return create_error_response(500, GENERIC_SERVER_ERROR_MESSAGE)
    finally:
        for upload in files:
            try:
                await upload.close()
            except Exception:
                logger.warning("Failed to close upload file.", exc_info=True)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)