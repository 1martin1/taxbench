import os
import shutil
import subprocess
import tempfile
from contextlib import suppress

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse


app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)


def _error_response_schema() -> dict:
    return {
        "description": "Error response",
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
    }


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
async def convert(file: UploadFile = File(...)) -> PlainTextResponse:
    if file is None:
        raise HTTPException(status_code=400, detail={"error": "Missing file"})

    if not file.filename:
        raise HTTPException(status_code=400, detail={"error": "Missing file name"})

    content_type = (file.content_type or "").lower()
    filename_lower = file.filename.lower()
    if content_type not in {"application/pdf", "application/octet-stream"} and not filename_lower.endswith(".pdf"):
        raise HTTPException(status_code=400, detail={"error": "Uploaded file must be a PDF"})

    if shutil.which("pdftotext") is None:
        raise HTTPException(status_code=500, detail={"error": "pdftotext utility is not available"})

    temp_pdf_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_pdf:
            temp_pdf_path = temp_pdf.name
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                temp_pdf.write(chunk)

        result = subprocess.run(
            ["pdftotext", "-", "-"],
            input=open(temp_pdf_path, "rb").read(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        if result.returncode != 0:
            stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
            raise HTTPException(
                status_code=500,
                detail={"error": f"Conversion failed: {stderr_text or 'unknown error'}"},
            )

        text_output = result.stdout.decode("utf-8", errors="replace")
        return PlainTextResponse(content=text_output, status_code=200)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"error": f"Conversion failed: {str(exc)}"})
    finally:
        await file.close()
        if temp_pdf_path:
            with suppress(FileNotFoundError):
                os.remove(temp_pdf_path)


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        return fastapi_json_response(status_code=exc.status_code, content=detail)
    if isinstance(detail, str):
        return fastapi_json_response(status_code=exc.status_code, content={"error": detail})
    return fastapi_json_response(status_code=exc.status_code, content={"error": "Request failed"})


def fastapi_json_response(status_code: int, content: dict):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content=content)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)