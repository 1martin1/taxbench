import asyncio
import os
import subprocess
import tempfile
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse

try:
    import multipart  # noqa: F401
    MULTIPART_AVAILABLE = True
except Exception:
    MULTIPART_AVAILABLE = False

app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)

MAX_UPLOAD_SIZE = 10 * 1024 * 1024
MAX_OUTPUT_SIZE = 10 * 1024 * 1024
CONVERSION_TIMEOUT_SECONDS = 120
MAX_CONCURRENT_CONVERSIONS = 2
PDF_MAGIC = b"%PDF-"

conversion_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CONVERSIONS)


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def is_missing_file_validation_error(exc: RequestValidationError) -> bool:
    for error in exc.errors():
        location = error.get("loc", ())
        if len(location) >= 2 and location[0] == "body" and location[1] == "file":
            return True
    return False


async def save_upload_with_limits(upload: UploadFile, destination_path: str) -> Optional[str]:
    total_written = 0
    first_chunk = b""
    first_non_empty_chunk_seen = False

    try:
        with open(destination_path, "wb") as output_file:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break

                if not first_non_empty_chunk_seen:
                    first_chunk = chunk
                    first_non_empty_chunk_seen = True

                total_written += len(chunk)
                if total_written > MAX_UPLOAD_SIZE:
                    return "Invalid input or missing file"

                output_file.write(chunk)
    finally:
        await upload.close()

    if total_written == 0:
        return "Invalid input or missing file"

    if not first_chunk.startswith(PDF_MAGIC):
        return "Invalid input or missing file"

    return None


def read_text_with_limit(path: str) -> str:
    chunks = []
    total_size = 0

    with open(path, "r", encoding="utf-8", errors="replace") as txt_file:
        while True:
            chunk = txt_file.read(1024 * 1024)
            if not chunk:
                break
            total_size += len(chunk.encode("utf-8", errors="replace"))
            if total_size > MAX_OUTPUT_SIZE:
                raise ValueError("output_too_large")
            chunks.append(chunk)

    return "".join(chunks)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path == "/convert" and request.method.upper() == "POST" and is_missing_file_validation_error(exc):
        return error_response("Invalid input or missing file", 400)
    return error_response("Invalid input or missing file", 400)


@app.post(
    "/convert",
    summary="Convert a PDF file to Text",
    description="Upload a PDF file to convert it into Text format.",
    responses={
        200: {
            "description": "Successfully converted PDF to text",
            "content": {
                "text/plain": {
                    "schema": {
                        "type": "string",
                        "description": "The converted text content.",
                    }
                }
            },
        },
        400: {
            "description": "Bad Request - Invalid input or missing file",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "error": {
                                "type": "string",
                                "description": "Error message",
                            }
                        },
                    }
                }
            },
        },
        500: {
            "description": "Internal Server Error - Conversion failed",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "error": {
                                "type": "string",
                                "description": "Error message",
                            }
                        },
                    }
                }
            },
        },
    },
)
async def convert_pdf_to_text(
    file: Optional[UploadFile] = File(None, description="The PDF file to be converted.")
) -> Response:
    if not MULTIPART_AVAILABLE:
        return error_response("Conversion failed", 500)

    if file is None:
        return error_response("Invalid input or missing file", 400)

    filename = (file.filename or "").strip()
    content_type = (file.content_type or "").strip().lower()

    if not filename:
        await file.close()
        return error_response("Invalid input or missing file", 400)

    if content_type and content_type not in {"application/pdf", "application/x-pdf"}:
        await file.close()
        return error_response("Invalid input or missing file", 400)

    async with conversion_semaphore:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_pdf_path = os.path.join(temp_dir, "input.pdf")
            output_txt_path = os.path.join(temp_dir, "output.txt")

            save_error = await save_upload_with_limits(file, input_pdf_path)
            if save_error is not None:
                return error_response(save_error, 400)

            try:
                result = subprocess.run(
                    ["pdftotext", input_pdf_path, output_txt_path],
                    capture_output=True,
                    text=True,
                    timeout=CONVERSION_TIMEOUT_SECONDS,
                    check=False,
                )
            except Exception:
                return error_response("Conversion failed", 500)

            if result.returncode != 0:
                stderr = (result.stderr or "").lower()
                if "syntax error" in stderr or "couldn't read xref table" in stderr or "may not be a pdf file" in stderr:
                    return error_response("Invalid input or missing file", 400)
                return error_response("Conversion failed", 500)

            try:
                text_content = read_text_with_limit(output_txt_path)
            except FileNotFoundError:
                return error_response("Conversion failed", 500)
            except ValueError:
                return error_response("Conversion failed", 500)
            except Exception:
                return error_response("Conversion failed", 500)

            return PlainTextResponse(content=text_content, status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)