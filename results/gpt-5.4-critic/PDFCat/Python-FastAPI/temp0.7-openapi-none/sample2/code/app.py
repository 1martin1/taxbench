import os
import shutil
import subprocess
import tempfile
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
import uvicorn


PDF_CONTENT_TYPE = "application/pdf"
ERROR_INVALID_INPUT = "Invalid input or missing files."
ERROR_PROCESSING = "An error occurred while processing the files."

MAX_FILES = 20
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_SIZE_BYTES = 100 * 1024 * 1024
MAX_OUTPUT_SIZE_BYTES = 150 * 1024 * 1024
PDFUNITE_TIMEOUT_SECONDS = 30
PDF_HEADER = b"%PDF-"


def create_error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def is_pdf_upload(upload: UploadFile) -> bool:
    filename = upload.filename or ""
    content_type = upload.content_type or ""
    return filename.lower().endswith(".pdf") and content_type == PDF_CONTENT_TYPE


def is_pdf_bytes(content: bytes) -> bool:
    if not content:
        return False
    return content.startswith(PDF_HEADER)


def get_file_size(file_obj) -> int:
    current_position = file_obj.tell()
    file_obj.seek(0, os.SEEK_END)
    size = file_obj.tell()
    file_obj.seek(current_position, os.SEEK_SET)
    return size


def write_upload_to_path(upload_file_obj, destination_path: str) -> int:
    upload_file_obj.seek(0)
    total_written = 0

    with open(destination_path, "wb") as output_file:
        while True:
            chunk = upload_file_obj.read(1024 * 1024)
            if not chunk:
                break
            total_written += len(chunk)
            if total_written > MAX_FILE_SIZE_BYTES:
                raise ValueError(ERROR_INVALID_INPUT)
            output_file.write(chunk)

    return total_written


def read_file_bytes_with_limit(path: str, max_size: int) -> bytes:
    file_size = os.path.getsize(path)
    if file_size <= 0 or file_size > max_size:
        raise ValueError(ERROR_PROCESSING)

    with open(path, "rb") as file_handle:
        return file_handle.read()


def run_pdfunite(command: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=PDFUNITE_TIMEOUT_SECONDS,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return create_error_response(400, ERROR_INVALID_INPUT)


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
async def concatenate(files: List[UploadFile] = File(...)) -> Response:
    if shutil.which("pdfunite") is None:
        return create_error_response(500, ERROR_PROCESSING)

    if not files or len(files) > MAX_FILES:
        return create_error_response(400, ERROR_INVALID_INPUT)

    total_size = 0

    try:
        for upload in files:
            if upload is None or not is_pdf_upload(upload):
                return create_error_response(400, ERROR_INVALID_INPUT)

            if upload.file is None:
                return create_error_response(400, ERROR_INVALID_INPUT)

            file_size = await run_in_threadpool(get_file_size, upload.file)
            if file_size <= 0 or file_size > MAX_FILE_SIZE_BYTES:
                return create_error_response(400, ERROR_INVALID_INPUT)

            total_size += file_size
            if total_size > MAX_TOTAL_SIZE_BYTES:
                return create_error_response(400, ERROR_INVALID_INPUT)

            await upload.seek(0)
            header = await upload.read(len(PDF_HEADER))
            if not is_pdf_bytes(header):
                return create_error_response(400, ERROR_INVALID_INPUT)
            await upload.seek(0)

        with tempfile.TemporaryDirectory() as temp_dir:
            input_paths = []

            for index, upload in enumerate(files):
                input_path = os.path.join(temp_dir, f"input_{index}.pdf")
                try:
                    await run_in_threadpool(write_upload_to_path, upload.file, input_path)
                except ValueError:
                    return create_error_response(400, ERROR_INVALID_INPUT)

                if not os.path.exists(input_path) or os.path.getsize(input_path) <= 0:
                    return create_error_response(400, ERROR_INVALID_INPUT)

                input_paths.append(input_path)

            output_path = os.path.join(temp_dir, "concatenated.pdf")
            command = ["pdfunite", *input_paths, output_path]

            try:
                result = await run_in_threadpool(run_pdfunite, command)
            except subprocess.TimeoutExpired:
                return create_error_response(500, ERROR_PROCESSING)

            if result.returncode != 0 or not os.path.exists(output_path):
                stderr_text = (result.stderr or "").lower()
                if "pdf" in stderr_text or "syntax" in stderr_text or "error" in stderr_text:
                    return create_error_response(400, ERROR_INVALID_INPUT)
                return create_error_response(500, ERROR_PROCESSING)

            try:
                merged_content = await run_in_threadpool(
                    read_file_bytes_with_limit, output_path, MAX_OUTPUT_SIZE_BYTES
                )
            except ValueError:
                return create_error_response(500, ERROR_PROCESSING)

            if not is_pdf_bytes(merged_content[: len(PDF_HEADER)]):
                return create_error_response(500, ERROR_PROCESSING)

            return Response(
                content=merged_content,
                media_type=PDF_CONTENT_TYPE,
                headers={
                    "Content-Disposition": 'attachment; filename="concatenated.pdf"'
                },
            )
    except OSError:
        return create_error_response(500, ERROR_PROCESSING)
    except subprocess.SubprocessError:
        return create_error_response(500, ERROR_PROCESSING)
    finally:
        for upload in files:
            try:
                await upload.close()
            except Exception:
                pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)