import os
import shutil
import subprocess
import tempfile
from contextlib import suppress

import uvicorn
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)


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
)
async def convert(file: UploadFile = File(...)) -> PlainTextResponse | JSONResponse:
    if file is None:
        return error_response("Missing file", 400)

    if not file.filename:
        return error_response("Missing file name", 400)

    content_type = (file.content_type or "").lower()
    if content_type and content_type not in {"application/pdf", "application/x-pdf"}:
        return error_response("Invalid file type. Only PDF files are supported.", 400)

    if shutil.which("pdftotext") is None:
        return error_response("pdftotext utility is not available on the server.", 500)

    temp_input_path = None
    temp_output_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_input:
            temp_input_path = temp_input.name
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                temp_input.write(chunk)

        if os.path.getsize(temp_input_path) == 0:
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
            stderr = (result.stderr or "").strip()
            message = "Conversion failed"
            if stderr:
                message = f"{message}: {stderr}"
            return error_response(message, 500)

        try:
            with open(temp_output_path, "r", encoding="utf-8") as output_file:
                text_content = output_file.read()
        except UnicodeDecodeError:
            with open(temp_output_path, "r", encoding="latin-1") as output_file:
                text_content = output_file.read()

        return PlainTextResponse(content=text_content, status_code=200)

    except subprocess.TimeoutExpired:
        return error_response("Conversion timed out.", 500)
    except Exception as exc:
        return error_response(f"Internal Server Error - Conversion failed: {exc}", 500)
    finally:
        await file.close()
        if temp_input_path:
            with suppress(FileNotFoundError):
                os.remove(temp_input_path)
        if temp_output_path:
            with suppress(FileNotFoundError):
                os.remove(temp_output_path)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)