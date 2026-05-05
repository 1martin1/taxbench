import asyncio
import os
import shutil
import subprocess
import tempfile
from typing import Optional

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
import uvicorn

MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
PDF_MAGIC = b"%PDF-"
PDF_HEADER_CHECK_SIZE = 1024
CONVERSION_TIMEOUT_SECONDS = 30
MAX_CONCURRENT_CONVERSIONS = 4


app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)

_conversion_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CONVERSIONS)


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def _is_pdf_header(data: bytes) -> bool:
    return PDF_MAGIC in data[:PDF_HEADER_CHECK_SIZE]


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
                        "$ref": "#/components/schemas/ErrorResponse"
                    }
                }
            },
        },
        500: {
            "description": "Internal Server Error - Conversion failed",
            "content": {
                "application/json": {
                    "schema": {
                        "$ref": "#/components/schemas/ErrorResponse"
                    }
                }
            },
        },
    },
    response_class=PlainTextResponse,
)
async def convert_pdf_to_text(
    file: Optional[UploadFile] = File(default=None, alias="file")
) -> PlainTextResponse | JSONResponse:
    if file is None:
        return error_response(400, "Missing file")

    if not file.filename:
        await file.close()
        return error_response(400, "Missing file name")

    content_type = (file.content_type or "").lower()
    filename = file.filename.lower()
    if content_type and content_type != "application/pdf" and not filename.endswith(".pdf"):
        await file.close()
        return error_response(400, "Invalid file type. Expected a PDF file.")

    temp_dir = None
    temp_pdf_path = None

    try:
        temp_dir = tempfile.mkdtemp(prefix="pdf_to_text_")
        temp_pdf_path = os.path.join(temp_dir, "input.pdf")

        total_size = 0
        header_sample = b""

        with open(temp_pdf_path, "wb") as temp_pdf:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break

                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE_BYTES:
                    return error_response(400, "Uploaded file is too large")

                if len(header_sample) < PDF_HEADER_CHECK_SIZE:
                    remaining = PDF_HEADER_CHECK_SIZE - len(header_sample)
                    header_sample += chunk[:remaining]

                temp_pdf.write(chunk)

        if total_size == 0:
            return error_response(400, "Uploaded file is empty")

        if not _is_pdf_header(header_sample):
            return error_response(400, "Invalid file type. Expected a PDF file.")

        async with _conversion_semaphore:
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["pdftotext", "-", temp_pdf_path],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=CONVERSION_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                return error_response(500, "Conversion failed")
            except FileNotFoundError:
                return error_response(500, "Conversion failed")

        if result.returncode != 0:
            return error_response(500, "Conversion failed")

        return PlainTextResponse(content=result.stdout, status_code=200)

    except Exception:
        return error_response(500, "Internal server error")
    finally:
        await file.close()
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)