import os
import shutil
import subprocess
import tempfile
from contextlib import suppress
from typing import List, Optional

import anyio
import uvicorn
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response


MAX_FILES = 50
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_SIZE_BYTES = 100 * 1024 * 1024
PDFUNITE_TIMEOUT_SECONDS = 30
PDF_HEADER = b"%PDF-"


app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
)


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return error_response("Invalid input or missing files.", 400)


def _validate_pdf_metadata(uploaded_file: UploadFile) -> bool:
    filename = uploaded_file.filename or ""
    if not filename.lower().endswith(".pdf"):
        return False

    content_type = (uploaded_file.content_type or "").lower()
    if content_type not in {"application/pdf", "application/x-pdf"}:
        return False

    return True


async def _save_and_validate_upload(
    uploaded_file: UploadFile,
    destination_path: str,
    total_size_so_far: int,
) -> int:
    first_chunk = True
    file_size = 0

    with open(destination_path, "wb") as out_file:
        while True:
            chunk = await uploaded_file.read(1024 * 1024)
            if not chunk:
                break

            if first_chunk:
                if not chunk.startswith(PDF_HEADER):
                    raise ValueError("Invalid input or missing files.")
                first_chunk = False

            file_size += len(chunk)
            total_size_so_far += len(chunk)

            if file_size > MAX_FILE_SIZE_BYTES or total_size_so_far > MAX_TOTAL_SIZE_BYTES:
                raise ValueError("Invalid input or missing files.")

            out_file.write(chunk)

    if first_chunk or file_size == 0:
        raise ValueError("Invalid input or missing files.")

    return total_size_so_far


def _run_pdfunite(input_paths: List[str], output_path: str) -> bool:
    command = ["pdfunite", *input_paths, output_path]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=PDFUNITE_TIMEOUT_SECONDS,
    )
    return result.returncode == 0 and os.path.exists(output_path)


def _read_output_file(output_path: str) -> bytes:
    with open(output_path, "rb") as f:
        return f.read()


@app.post(
    "/concatenate",
    summary="Concatenate multiple PDF files",
    description="Accepts multiple PDF files and returns a single concatenated PDF.",
    responses={
        200: {
            "description": "Successfully concatenated the PDF files.",
            "content": {
                "application/pdf": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        },
        400: {
            "description": "Bad Request - Invalid input or missing files.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "error": {
                                "type": "string",
                                "example": "Invalid input or missing files.",
                            }
                        },
                    }
                }
            },
        },
        500: {
            "description": "Internal Server Error - An error occurred while processing the files.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "error": {
                                "type": "string",
                                "example": "An error occurred while processing the files.",
                            }
                        },
                    }
                }
            },
        },
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "files": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                                "description": "An array of PDF files to concatenate.",
                            }
                        },
                        "required": ["files"],
                    }
                }
            },
        }
    },
)
async def concatenate_pdfs(
    files: Optional[List[UploadFile]] = File(default=None)
) -> Response:
    if not files:
        return error_response("Invalid input or missing files.", 400)

    if len(files) > MAX_FILES:
        return error_response("Invalid input or missing files.", 400)

    if shutil.which("pdfunite") is None:
        return error_response("An error occurred while processing the files.", 500)

    temp_dir = tempfile.mkdtemp(prefix="pdf_concat_")
    input_paths: List[str] = []
    output_path = os.path.join(temp_dir, "concatenated.pdf")
    total_size = 0

    try:
        for index, uploaded_file in enumerate(files):
            if uploaded_file is None or not _validate_pdf_metadata(uploaded_file):
                return error_response("Invalid input or missing files.", 400)

            input_path = os.path.join(temp_dir, f"input_{index + 1}.pdf")
            try:
                total_size = await _save_and_validate_upload(
                    uploaded_file=uploaded_file,
                    destination_path=input_path,
                    total_size_so_far=total_size,
                )
            except ValueError:
                return error_response("Invalid input or missing files.", 400)

            input_paths.append(input_path)

        if not input_paths:
            return error_response("Invalid input or missing files.", 400)

        try:
            success = await anyio.to_thread.run_sync(
                _run_pdfunite, input_paths, output_path
            )
        except subprocess.TimeoutExpired:
            return error_response("An error occurred while processing the files.", 500)
        except FileNotFoundError:
            return error_response("An error occurred while processing the files.", 500)
        except Exception:
            return error_response("An error occurred while processing the files.", 500)

        if not success:
            return error_response("An error occurred while processing the files.", 500)

        try:
            merged_pdf = await anyio.to_thread.run_sync(_read_output_file, output_path)
        except Exception:
            return error_response("An error occurred while processing the files.", 500)

        if not merged_pdf.startswith(PDF_HEADER):
            return error_response("An error occurred while processing the files.", 500)

        return Response(
            content=merged_pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="concatenated.pdf"'},
        )

    finally:
        for uploaded_file in files:
            with suppress(Exception):
                await uploaded_file.close()
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)