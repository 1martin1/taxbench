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
async def convert_pdf_to_text(file: UploadFile = File(..., description="The PDF file to be converted.")):
    if file is None:
        return error_response("Missing file", 400)

    if not file.filename:
        return error_response("Missing file", 400)

    content_type = (file.content_type or "").lower()
    if content_type and content_type != "application/pdf":
        return error_response("Invalid file type. Expected a PDF file.", 400)

    temp_pdf_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
            temp_pdf_path = temp_pdf.name
            await file.seek(0)
            shutil.copyfileobj(file.file, temp_pdf)

        command = ["pdftotext", temp_pdf_path, "-"]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        if result.returncode != 0:
            stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
            message = "Conversion failed"
            if stderr_text:
                message = f"Conversion failed: {stderr_text}"
            return error_response(message, 500)

        text_output = result.stdout.decode("utf-8", errors="replace")
        return PlainTextResponse(content=text_output, status_code=200)

    except Exception:
        return error_response("Internal Server Error - Conversion failed", 500)
    finally:
        await file.close()
        if temp_pdf_path:
            with suppress(FileNotFoundError):
                os.remove(temp_pdf_path)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)