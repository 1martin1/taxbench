import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from contextlib import asynccontextmanager
from typing import Any, List

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from starlette.requests import ClientDisconnect

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_FILES = 20
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_SIZE_BYTES = 100 * 1024 * 1024
PDFUNITE_TIMEOUT_SECONDS = 30
MAX_CONCURRENT_JOBS = 4
PDF_HEADER = b"%PDF-"
PDF_EOF_MARKER = b"%%EOF"


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def is_valid_pdf_file(path: str) -> bool:
    try:
        file_size = os.path.getsize(path)
        if file_size < len(PDF_HEADER):
            return False

        with open(path, "rb") as f:
            header = f.read(5)
            if header != PDF_HEADER:
                return False

            tail_read_size = min(file_size, 4096)
            f.seek(-tail_read_size, os.SEEK_END)
            tail = f.read(tail_read_size)
            if PDF_EOF_MARKER not in tail:
                return False

        return True
    except OSError:
        return False


def concatenate_with_pdfunite(input_paths: List[str], output_path: str) -> bool:
    command = ["pdfunite", *input_paths, output_path]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=PDFUNITE_TIMEOUT_SECONDS,
        )
        return result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except subprocess.TimeoutExpired:
        logger.warning("pdfunite timed out")
        return False
    except OSError:
        logger.exception("Failed to execute pdfunite")
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    if shutil.which("pdfunite") is None:
        raise RuntimeError("pdfunite is required but not available in PATH")
    app.state.concat_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
    yield


app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(_: Request, __: RequestValidationError) -> JSONResponse:
    return error_response(400, "Invalid input or missing files.")


@app.exception_handler(MultiPartException)
async def multipart_exception_handler(_: Request, __: MultiPartException) -> JSONResponse:
    return error_response(400, "Invalid input or missing files.")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code in (400, 404, 405, 413, 415, 422):
        return error_response(400, "Invalid input or missing files.")
    return error_response(500, "An error occurred while processing the files.")


@app.exception_handler(ClientDisconnect)
async def client_disconnect_handler(_: Request, __: ClientDisconnect) -> JSONResponse:
    return error_response(400, "Invalid input or missing files.")


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
async def concatenate_pdfs(request: Request):
    temp_dir = tempfile.mkdtemp(prefix="pdf_concat_")
    uploaded_files: List[Any] = []
    input_paths: List[str] = []
    total_size = 0

    try:
        content_type = request.headers.get("content-type", "")
        if "multipart/form-data" not in content_type.lower():
            return error_response(400, "Invalid input or missing files.")

        try:
            form = await request.form()
        except (RequestValidationError, MultiPartException, ValueError):
            return error_response(400, "Invalid input or missing files.")

        files = form.getlist("files")
        if not files:
            return error_response(400, "Invalid input or missing files.")
        if len(files) > MAX_FILES:
            return error_response(400, "Invalid input or missing files.")

        for index, upload in enumerate(files):
            if not hasattr(upload, "filename") or not hasattr(upload, "read") or not hasattr(upload, "close"):
                return error_response(400, "Invalid input or missing files.")

            uploaded_files.append(upload)

            if not upload.filename:
                return error_response(400, "Invalid input or missing files.")

            content_type_value = (getattr(upload, "content_type", "") or "").lower()
            if content_type_value != "application/pdf":
                return error_response(400, "Invalid input or missing files.")

            input_path = os.path.join(temp_dir, f"input_{index}.pdf")
            bytes_written = 0

            try:
                with open(input_path, "wb") as out_file:
                    while True:
                        chunk = await upload.read(1024 * 1024)
                        if not chunk:
                            break
                        bytes_written += len(chunk)
                        total_size += len(chunk)

                        if bytes_written > MAX_FILE_SIZE_BYTES or total_size > MAX_TOTAL_SIZE_BYTES:
                            return error_response(400, "Invalid input or missing files.")

                        out_file.write(chunk)
            except OSError:
                logger.exception("Failed to write uploaded file")
                return error_response(500, "An error occurred while processing the files.")

            if bytes_written == 0:
                return error_response(400, "Invalid input or missing files.")

            if not is_valid_pdf_file(input_path):
                return error_response(400, "Invalid input or missing files.")

            input_paths.append(input_path)

        if not input_paths:
            return error_response(400, "Invalid input or missing files.")

        output_path = os.path.join(temp_dir, "concatenated.pdf")

        async with request.app.state.concat_semaphore:
            success = await asyncio.to_thread(concatenate_with_pdfunite, input_paths, output_path)

        if not success or not is_valid_pdf_file(output_path):
            return error_response(500, "An error occurred while processing the files.")

        try:
            with open(output_path, "rb") as f:
                output_bytes = f.read()
        except OSError:
            logger.exception("Failed to read output PDF")
            return error_response(500, "An error occurred while processing the files.")

        return Response(
            content=output_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="concatenated.pdf"'},
        )
    except ClientDisconnect:
        return error_response(400, "Invalid input or missing files.")
    except Exception:
        logger.exception("Unhandled error during PDF concatenation")
        return error_response(500, "An error occurred while processing the files.")
    finally:
        for upload in uploaded_files:
            try:
                await upload.close()
            except Exception:
                logger.warning("Failed to close upload file", exc_info=True)
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)