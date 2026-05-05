import asyncio
import os
import shutil
import subprocess
import tempfile
from contextlib import suppress
from typing import Any

import uvicorn
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse

MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_SIZE_BYTES = 20 * 1024 * 1024
READ_CHUNK_SIZE = 1024 * 1024
PDF_CONVERSION_TIMEOUT_SECONDS = 30
MAX_CONCURRENT_CONVERSIONS = 4

app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)

_conversion_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CONVERSIONS)


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return error_response("Invalid input or missing file", 400)


def _error_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "error": {
                "type": "string",
                "description": "Error message",
            }
        },
    }


def _is_pdf_header(data: bytes) -> bool:
    return data.startswith(b"%PDF-")


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
                    "schema": _error_schema()
                }
            },
        },
        500: {
            "description": "Internal Server Error - Conversion failed",
            "content": {
                "application/json": {
                    "schema": _error_schema()
                }
            },
        },
    },
)
async def convert(file: UploadFile = File(...)) -> PlainTextResponse | JSONResponse:
    if not file.filename:
        await file.close()
        return error_response("Missing file name", 400)

    content_type = (file.content_type or "").lower()
    if content_type and content_type != "application/pdf":
        await file.close()
        return error_response("Invalid file type. Expected a PDF file.", 400)

    if shutil.which("pdftotext") is None:
        await file.close()
        return error_response("pdftotext utility is not available", 500)

    input_path = None
    output_path = None

    try:
        async with _conversion_semaphore:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as input_tmp:
                input_path = input_tmp.name
                total_size = 0
                first_chunk = True
                while True:
                    chunk = await file.read(READ_CHUNK_SIZE)
                    if not chunk:
                        break

                    if first_chunk:
                        first_chunk = False
                        if not _is_pdf_header(chunk):
                            return error_response("Invalid file type. Expected a PDF file.", 400)

                    total_size += len(chunk)
                    if total_size > MAX_UPLOAD_SIZE_BYTES:
                        return error_response("Uploaded file is too large", 400)

                    input_tmp.write(chunk)

            if input_path is None or os.path.getsize(input_path) == 0:
                return error_response("Uploaded file is empty", 400)

            with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as output_tmp:
                output_path = output_tmp.name

            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["pdftotext", input_path, output_path],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=PDF_CONVERSION_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                return error_response("Internal Server Error - Conversion failed", 500)

            if result.returncode != 0:
                return error_response("Internal Server Error - Conversion failed", 500)

            if not os.path.exists(output_path):
                return error_response("Internal Server Error - Conversion failed", 500)

            output_size = os.path.getsize(output_path)
            if output_size > MAX_OUTPUT_SIZE_BYTES:
                return error_response("Internal Server Error - Conversion failed", 500)

            try:
                with open(output_path, "r", encoding="utf-8") as f:
                    text_content = f.read(MAX_OUTPUT_SIZE_BYTES + 1)
            except UnicodeDecodeError:
                with open(output_path, "r", encoding="latin-1") as f:
                    text_content = f.read(MAX_OUTPUT_SIZE_BYTES + 1)

            if len(text_content.encode("utf-8", errors="ignore")) > MAX_OUTPUT_SIZE_BYTES:
                return error_response("Internal Server Error - Conversion failed", 500)

            return PlainTextResponse(content=text_content, status_code=200)
    finally:
        await file.close()
        for path in (input_path, output_path):
            if path:
                with suppress(FileNotFoundError):
                    os.remove(path)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)