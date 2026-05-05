import os
import shutil
import subprocess
import tempfile
from contextlib import suppress

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
import uvicorn


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
        400: {"description": "Bad Request - Invalid input or missing file"},
        500: {"description": "Internal Server Error - Conversion failed"},
    },
    response_class=PlainTextResponse,
)
async def convert_pdf_to_text(file: UploadFile = File(...)) -> PlainTextResponse | JSONResponse:
    if file is None:
        return error_response(400, "Missing file")

    filename = file.filename or ""
    content_type = file.content_type or ""

    if not filename:
        return error_response(400, "Missing file name")

    if not filename.lower().endswith(".pdf") and content_type not in ("application/pdf", "application/octet-stream"):
        return error_response(400, "Invalid file type. Expected a PDF file.")

    if shutil.which("pdftotext") is None:
        return error_response(500, "pdftotext utility is not available")

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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            error_message = result.stderr.strip() or "Conversion failed"
            return error_response(500, error_message)

        try:
            with open(temp_txt_path, "r", encoding="utf-8") as f:
                text_content = f.read()
        except UnicodeDecodeError:
            with open(temp_txt_path, "r", encoding="latin-1") as f:
                text_content = f.read()

        return PlainTextResponse(content=text_content, status_code=200)

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