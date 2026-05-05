import os
import shutil
import subprocess
import tempfile

import uvicorn
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MiB
PDF_MAGIC = b"%PDF-"
PDF_MIME_TYPES = {"application/pdf", "application/x-pdf"}


def error_response(message: str, status_code: int) -> JSONResponse:
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
    response_class=PlainTextResponse,
)
async def convert_pdf_to_text(file: UploadFile | None = File(None)):
    if file is None:
        return error_response("Bad Request - Invalid input or missing file", 400)

    if shutil.which("pdftotext") is None:
        await file.close()
        return error_response("Internal Server Error - Conversion failed", 500)

    filename = (file.filename or "").strip()
    content_type = (file.content_type or "").lower().strip()

    if not filename.lower().endswith(".pdf"):
        await file.close()
        return error_response("Bad Request - Invalid input or missing file", 400)

    if content_type and content_type not in PDF_MIME_TYPES:
        await file.close()
        return error_response("Bad Request - Invalid input or missing file", 400)

    temp_pdf_path = None
    temp_txt_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
            temp_pdf_path = temp_pdf.name

            total_size = 0
            first_chunk = b""
            first_chunk_checked = False

            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break

                if not first_chunk_checked:
                    first_chunk = chunk
                    first_chunk_checked = True
                    if not first_chunk.startswith(PDF_MAGIC):
                        return error_response("Bad Request - Invalid input or missing file", 400)

                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE:
                    return error_response("Bad Request - Invalid input or missing file", 400)

                temp_pdf.write(chunk)

        if not first_chunk_checked:
            return error_response("Bad Request - Invalid input or missing file", 400)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as temp_txt:
            temp_txt_path = temp_txt.name

        result = subprocess.run(
            ["pdftotext", temp_pdf_path, temp_txt_path],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

        if result.returncode != 0:
            return error_response("Internal Server Error - Conversion failed", 500)

        with open(temp_txt_path, "r", encoding="utf-8", errors="replace") as text_file:
            text_content = text_file.read()

        return PlainTextResponse(content=text_content, status_code=200)

    except subprocess.TimeoutExpired:
        return error_response("Internal Server Error - Conversion failed", 500)
    except OSError:
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