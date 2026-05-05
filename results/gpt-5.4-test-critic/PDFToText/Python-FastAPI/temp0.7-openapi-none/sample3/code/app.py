import os
import shutil
import subprocess
import tempfile
from contextlib import suppress
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel


MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MiB
READ_CHUNK_SIZE = 1024 * 1024  # 1 MiB
PDF_SIGNATURE = b"%PDF-"


class ErrorResponse(BaseModel):
    error: str


app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


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
                    "schema": ErrorResponse.model_json_schema()
                }
            },
        },
        500: {
            "description": "Internal Server Error - Conversion failed",
            "content": {
                "application/json": {
                    "schema": ErrorResponse.model_json_schema()
                }
            },
        },
    },
    response_class=PlainTextResponse,
)
async def convert_pdf_to_text(file: Optional[UploadFile] = File(None)):
    if file is None:
        return error_response(400, "Missing file")

    filename = file.filename or ""
    if not filename:
        await file.close()
        return error_response(400, "Missing file name")

    if shutil.which("pdftotext") is None:
        await file.close()
        return error_response(500, "Conversion failed")

    temp_pdf_path = None
    temp_txt_path = None
    total_size = 0
    first_chunk = b""
    saw_data = False

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
            temp_pdf_path = temp_pdf.name

            while True:
                chunk = await file.read(READ_CHUNK_SIZE)
                if not chunk:
                    break

                if not saw_data:
                    first_chunk = chunk
                    saw_data = True

                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE_BYTES:
                    return error_response(400, "File too large")

                temp_pdf.write(chunk)

        if not saw_data:
            return error_response(400, "Invalid file type. Expected a PDF file.")

        if not first_chunk.startswith(PDF_SIGNATURE):
            return error_response(400, "Invalid file type. Expected a PDF file.")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as temp_txt:
            temp_txt_path = temp_txt.name

        result = subprocess.run(
            ["pdftotext", temp_pdf_path, temp_txt_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )

        if result.returncode != 0:
            return error_response(500, "Conversion failed")

        try:
            with open(temp_txt_path, "r", encoding="utf-8") as f:
                text_content = f.read()
        except UnicodeDecodeError:
            with open(temp_txt_path, "r", encoding="latin-1") as f:
                text_content = f.read()

        return PlainTextResponse(content=text_content, status_code=200)

    except subprocess.TimeoutExpired:
        return error_response(500, "Conversion failed")
    except Exception:
        return error_response(500, "Conversion failed")
    finally:
        await file.close()
        if temp_pdf_path:
            with suppress(FileNotFoundError):
                os.remove(temp_pdf_path)
        if temp_txt_path:
            with suppress(FileNotFoundError):
                os.remove(temp_txt_path)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)