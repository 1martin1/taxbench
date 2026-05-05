import asyncio
import os
import shutil
import subprocess
import tempfile
from contextlib import suppress
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse

app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MiB
MAX_OUTPUT_SIZE = 20 * 1024 * 1024  # 20 MiB
PDFTOTEXT_TIMEOUT_SECONDS = 30
MAX_CONCURRENT_CONVERSIONS = 4
PDF_MAGIC = b"%PDF-"

conversion_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CONVERSIONS)


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return error_response("Bad Request - Invalid input or missing file", 400)


def _is_pdf_header(data: bytes) -> bool:
    return data.startswith(PDF_MAGIC)


def _read_text_file_with_fallback(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as output_file:
            return output_file.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="latin-1") as output_file:
            return output_file.read()


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
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {
                                "type": "string",
                                "format": "binary",
                                "description": "The PDF file to be converted.",
                            }
                        },
                    }
                }
            },
        }
    },
)
async def convert(
    file: Optional[UploadFile] = File(default=None)
) -> PlainTextResponse | JSONResponse:
    if file is None:
        return error_response("Missing file", 400)

    if shutil.which("pdftotext") is None:
        await file.close()
        return error_response("Internal Server Error - Conversion failed", 500)

    temp_input_path = None
    temp_output_path = None
    total_size = 0
    first_chunk = b""

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_input:
            temp_input_path = temp_input.name

            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break

                if not first_chunk:
                    first_chunk = chunk[: len(PDF_MAGIC)]

                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE:
                    return error_response("Uploaded file is too large.", 400)

                temp_input.write(chunk)

        if total_size == 0:
            return error_response("Uploaded file is empty.", 400)

        if not _is_pdf_header(first_chunk):
            return error_response("Invalid file type. Please upload a PDF file.", 400)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as temp_output:
            temp_output_path = temp_output.name

        async with conversion_semaphore:
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["pdftotext", temp_input_path, temp_output_path],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=PDFTOTEXT_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                return error_response("Internal Server Error - Conversion failed", 500)

        if result.returncode != 0:
            return error_response("Internal Server Error - Conversion failed", 500)

        output_size = os.path.getsize(temp_output_path)
        if output_size > MAX_OUTPUT_SIZE:
            return error_response("Internal Server Error - Conversion failed", 500)

        text_content = _read_text_file_with_fallback(temp_output_path)
        return PlainTextResponse(content=text_content, status_code=200)

    except Exception:
        return error_response("Internal Server Error - Conversion failed", 500)
    finally:
        await file.close()
        for path in (temp_input_path, temp_output_path):
            if path:
                with suppress(FileNotFoundError):
                    os.remove(path)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)