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
GENERIC_INTERNAL_ERROR_MESSAGE = "An error occurred while processing the files."

MAX_FILES = 20
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_SIZE_BYTES = 100 * 1024 * 1024
MAX_OUTPUT_SIZE_BYTES = 150 * 1024 * 1024
PDFUNITE_TIMEOUT_SECONDS = 30
PDF_HEADER = b"%PDF-"


def json_error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def is_pdf_header(data: bytes) -> bool:
    return data.startswith(PDF_HEADER)


def safe_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def multipart_available() -> bool:
    try:
        import multipart  # noqa: F401
        return True
    except Exception:
        return False


def pdfunite_available() -> bool:
    return shutil.which("pdfunite") is not None


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.multipart_available = multipart_available()
    app.state.pdfunite_available = pdfunite_available()
    yield


app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return json_error(GENERIC_BAD_REQUEST_MESSAGE, 400)


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
async def concatenate(
    request: Request,
    files: List[UploadFile] = File(...),
):
    if not getattr(request.app.state, "multipart_available", False):
        return json_error(GENERIC_INTERNAL_ERROR_MESSAGE, 500)

    if not getattr(request.app.state, "pdfunite_available", False):
        return json_error(GENERIC_INTERNAL_ERROR_MESSAGE, 500)

    if not files:
        return json_error(GENERIC_BAD_REQUEST_MESSAGE, 400)

    if len(files) > MAX_FILES:
        return json_error(GENERIC_BAD_REQUEST_MESSAGE, 400)

    total_size = 0

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_paths: List[str] = []

            for index, upload in enumerate(files):
                input_path = os.path.join(temp_dir, f"input_{index}.pdf")
                bytes_written = 0
                first_chunk: Optional[bytes] = None
                has_any_content = False

                try:
                    with open(input_path, "wb") as output_file:
                        while True:
                            chunk = await upload.read(1024 * 1024)
                            if not chunk:
                                break

                            if first_chunk is None:
                                first_chunk = chunk

                            has_any_content = True
                            bytes_written += len(chunk)
                            total_size += len(chunk)

                            if bytes_written > MAX_FILE_SIZE_BYTES:
                                return json_error(GENERIC_BAD_REQUEST_MESSAGE, 400)

                            if total_size > MAX_TOTAL_SIZE_BYTES:
                                return json_error(GENERIC_BAD_REQUEST_MESSAGE, 400)

                            output_file.write(chunk)
                finally:
                    await upload.close()

                if not has_any_content:
                    safe_remove(input_path)
                    return json_error(GENERIC_BAD_REQUEST_MESSAGE, 400)

                if first_chunk is None or not is_pdf_header(first_chunk):
                    safe_remove(input_path)
                    return json_error(GENERIC_BAD_REQUEST_MESSAGE, 400)

                input_paths.append(input_path)

            output_path = os.path.join(temp_dir, "concatenated.pdf")
            command = ["pdfunite", *input_paths, output_path]

            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=PDFUNITE_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                return json_error(GENERIC_INTERNAL_ERROR_MESSAGE, 500)

            if result.returncode != 0 or not os.path.exists(output_path):
                return json_error(GENERIC_INTERNAL_ERROR_MESSAGE, 500)

            output_size = os.path.getsize(output_path)
            if output_size <= 0 or output_size > MAX_OUTPUT_SIZE_BYTES:
                return json_error(GENERIC_INTERNAL_ERROR_MESSAGE, 500)

            with open(output_path, "rb") as f:
                output_bytes = f.read()

            if not is_pdf_header(output_bytes[:5]):
                return json_error(GENERIC_INTERNAL_ERROR_MESSAGE, 500)

            return Response(
                content=output_bytes,
                media_type=PDF_CONTENT_TYPE,
                headers={
                    "Content-Disposition": 'attachment; filename="concatenated.pdf"'
                },
            )
    except Exception:
        return json_error(GENERIC_INTERNAL_ERROR_MESSAGE, 500)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)