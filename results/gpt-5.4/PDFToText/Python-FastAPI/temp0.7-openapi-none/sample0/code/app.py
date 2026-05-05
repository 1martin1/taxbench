import os
import shutil
import subprocess
import tempfile
from typing import Any

import uvicorn
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return error_response("Internal Server Error - Conversion failed", 500)


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
async def convert_pdf_to_text(file: UploadFile = File(...)) -> PlainTextResponse | JSONResponse:
    if file is None:
        return error_response("Bad Request - Invalid input or missing file", 400)

    filename = file.filename or ""
    content_type = (file.content_type or "").lower()

    if not filename.lower().endswith(".pdf") and content_type not in {
        "application/pdf",
        "application/x-pdf",
    }:
        return error_response("Bad Request - Invalid input or missing file", 400)

    if shutil.which("pdftotext") is None:
        return error_response("Internal Server Error - Conversion failed", 500)

    temp_pdf_path = None
    temp_txt_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
            temp_pdf_path = temp_pdf.name
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                temp_pdf.write(chunk)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as temp_txt:
            temp_txt_path = temp_txt.name

        result = subprocess.run(
            ["pdftotext", temp_pdf_path, temp_txt_path],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            return error_response("Internal Server Error - Conversion failed", 500)

        with open(temp_txt_path, "r", encoding="utf-8", errors="replace") as text_file:
            text_content = text_file.read()

        return PlainTextResponse(content=text_content, status_code=200)

    except Exception:
        return error_response("Internal Server Error - Conversion failed", 500)
    finally:
        await file.close()
        for path in (temp_pdf_path, temp_txt_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)