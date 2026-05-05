import asyncio
import os
import shutil
import tempfile
from contextlib import suppress
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse


MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024
PDF_SIGNATURE = b"%PDF-"
PDFTOTEXT_TIMEOUT_SECONDS = 30
MAX_CONCURRENT_CONVERSIONS = 4
CHUNK_SIZE = 1024 * 1024


app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)

_conversion_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CONVERSIONS)


def fastapi_error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": str(message)})


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException) -> JSONResponse:
    return fastapi_error_response(exc.status_code, exc.detail)


def _cleanup_upload_temp_file(path: Optional[str]) -> None:
    if path:
        with suppress(FileNotFoundError):
            os.remove(path)


def _ensure_pdftotext_available() -> None:
    if shutil.which("pdftotext") is None:
        raise HTTPException(status_code=500, detail="Conversion service unavailable")


async def _write_upload_to_temp_pdf(upload: UploadFile) -> str:
    temp_path: Optional[str] = None
    total_size = 0
    header = b""

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
            temp_path = temp_pdf.name

            while True:
                chunk = await upload.read(CHUNK_SIZE)
                if not chunk:
                    break

                if len(header) < len(PDF_SIGNATURE):
                    needed = len(PDF_SIGNATURE) - len(header)
                    header += chunk[:needed]

                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE_BYTES:
                    raise HTTPException(status_code=400, detail="Uploaded file is too large")

                await asyncio.to_thread(temp_pdf.write, chunk)

            await asyncio.to_thread(temp_pdf.flush)

        if total_size == 0:
            raise HTTPException(status_code=400, detail="Missing file")

        if not header.startswith(PDF_SIGNATURE):
            raise HTTPException(status_code=400, detail="Uploaded file must be a PDF")

        return temp_path
    except Exception:
        _cleanup_upload_temp_file(temp_path)
        raise


async def _run_pdftotext(temp_pdf_path: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "pdftotext",
        temp_pdf_path,
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(),
            timeout=PDFTOTEXT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        with suppress(Exception):
            await process.communicate()
        raise HTTPException(status_code=500, detail="Conversion failed")

    if process.returncode != 0:
        raise HTTPException(status_code=500, detail="Conversion failed")

    try:
        return stdout.decode("utf-8")
    except UnicodeDecodeError:
        return stdout.decode("utf-8", errors="replace")


@app.post(
    "/convert",
    summary="Convert a PDF file to Text",
    description="Upload a PDF file to convert it into Text format.",
    response_class=PlainTextResponse,
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
async def convert_pdf_to_text(file: UploadFile | None = File(None)) -> PlainTextResponse:
    if file is None:
        raise HTTPException(status_code=400, detail="Missing file")

    _ensure_pdftotext_available()

    temp_pdf_path: Optional[str] = None
    try:
        async with _conversion_semaphore:
            temp_pdf_path = await _write_upload_to_temp_pdf(file)
            text_content = await _run_pdftotext(temp_pdf_path)
            return PlainTextResponse(content=text_content, status_code=200)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Conversion failed")
    finally:
        _cleanup_upload_temp_file(temp_pdf_path)
        await file.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)