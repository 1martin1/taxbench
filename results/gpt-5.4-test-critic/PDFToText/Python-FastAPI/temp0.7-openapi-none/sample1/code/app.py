import logging
import os
import subprocess
import tempfile
from contextlib import suppress

import uvicorn
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MiB
PDF_MAGIC = b"%PDF-"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def is_pdf_header(data: bytes) -> bool:
    return data.startswith(PDF_MAGIC)


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
async def convert(file: UploadFile | None = File(None)):
    if file is None:
        return error_response("Missing file", 400)

    if not file.filename:
        await file.close()
        return error_response("Missing file name", 400)

    if shutil_which_pdftotext_missing():
        await file.close()
        return error_response("Internal Server Error - Conversion failed", 500)

    temp_input_path = None
    temp_output_path = None

    try:
        total_size = 0
        first_chunk = b""
        first_chunk_checked = False

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_input:
            temp_input_path = temp_input.name

            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break

                if not first_chunk_checked:
                    first_chunk = chunk
                    first_chunk_checked = True
                    if not is_pdf_header(first_chunk):
                        return error_response(
                            "Invalid file type. Only PDF files are supported.", 400
                        )

                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE_BYTES:
                    return error_response("Uploaded file is too large.", 400)

                temp_input.write(chunk)

        if total_size == 0:
            return error_response("Uploaded file is empty.", 400)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as temp_output:
            temp_output_path = temp_output.name

        result = subprocess.run(
            ["pdftotext", temp_input_path, temp_output_path],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

        if result.returncode != 0:
            logger.warning(
                "pdftotext failed with return code %s for uploaded file %s",
                result.returncode,
                file.filename,
            )
            return error_response("Internal Server Error - Conversion failed", 500)

        text_content = read_text_file(temp_output_path)

        if not text_content.strip():
            return error_response("Internal Server Error - Conversion failed", 500)

        return PlainTextResponse(content=text_content, status_code=200)

    except subprocess.TimeoutExpired:
        logger.warning("pdftotext timed out for uploaded file %s", file.filename)
        return error_response("Internal Server Error - Conversion failed", 500)
    except Exception:
        logger.exception("Unexpected error during PDF conversion")
        return error_response("Internal Server Error - Conversion failed", 500)
    finally:
        await file.close()
        if temp_input_path:
            with suppress(FileNotFoundError):
                os.remove(temp_input_path)
        if temp_output_path:
            with suppress(FileNotFoundError):
                os.remove(temp_output_path)


def read_text_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as output_file:
            return output_file.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="latin-1") as output_file:
            return output_file.read()


def shutil_which_pdftotext_missing() -> bool:
    import shutil

    return shutil.which("pdftotext") is None


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)