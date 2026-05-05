import logging
import os
import shutil
import subprocess
import tempfile
from typing import List

import uvicorn
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, Response

app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
)

logger = logging.getLogger("pdf_concatenation_service")
logging.basicConfig(level=logging.INFO)

MAX_FILES = 20
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_SIZE_BYTES = 100 * 1024 * 1024
MAX_OUTPUT_SIZE_BYTES = 150 * 1024 * 1024
READ_CHUNK_SIZE = 1024 * 1024
PDFUNITE_TIMEOUT_SECONDS = 30
PDF_HEADER = b"%PDF-"


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def is_valid_pdf_header(file_path: str) -> bool:
    try:
        with open(file_path, "rb") as file_obj:
            header = file_obj.read(len(PDF_HEADER))
        return header == PDF_HEADER
    except OSError:
        return False


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
)
async def concatenate(files: List[UploadFile] = File(...)):
    if not files:
        return error_response(400, "Invalid input or missing files.")

    if len(files) > MAX_FILES:
        return error_response(400, "Invalid input or missing files.")

    if shutil.which("pdfunite") is None:
        logger.error("pdfunite executable not found")
        return error_response(500, "An error occurred while processing the files.")

    total_size = 0

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_paths = []

            for index, uploaded_file in enumerate(files):
                if uploaded_file is None or not uploaded_file.filename:
                    return error_response(400, "Invalid input or missing files.")

                input_path = os.path.join(temp_dir, f"input_{index}.pdf")
                file_size = 0

                try:
                    with open(input_path, "wb") as destination:
                        while True:
                            chunk = await uploaded_file.read(READ_CHUNK_SIZE)
                            if not chunk:
                                break

                            file_size += len(chunk)
                            total_size += len(chunk)

                            if (
                                file_size > MAX_FILE_SIZE_BYTES
                                or total_size > MAX_TOTAL_SIZE_BYTES
                            ):
                                return error_response(400, "Invalid input or missing files.")

                            destination.write(chunk)
                finally:
                    await uploaded_file.close()

                if file_size == 0:
                    return error_response(400, "Invalid input or missing files.")

                if not is_valid_pdf_header(input_path):
                    return error_response(400, "Invalid input or missing files.")

                input_paths.append(input_path)

            output_path = os.path.join(temp_dir, "concatenated.pdf")
            command = ["pdfunite", *input_paths, output_path]

            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=PDFUNITE_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                logger.exception("pdfunite timed out")
                return error_response(500, "An error occurred while processing the files.")
            except OSError:
                logger.exception("Failed to execute pdfunite")
                return error_response(500, "An error occurred while processing the files.")

            if result.returncode != 0:
                logger.info("pdfunite rejected invalid input: %s", result.stderr.strip())
                return error_response(400, "Invalid input or missing files.")

            if not os.path.exists(output_path):
                logger.error("pdfunite did not produce an output file")
                return error_response(500, "An error occurred while processing the files.")

            try:
                output_size = os.path.getsize(output_path)
            except OSError:
                logger.exception("Failed to stat output file")
                return error_response(500, "An error occurred while processing the files.")

            if output_size <= 0 or output_size > MAX_OUTPUT_SIZE_BYTES:
                return error_response(500, "An error occurred while processing the files.")

            if not is_valid_pdf_header(output_path):
                logger.error("Output file is not a valid PDF")
                return error_response(500, "An error occurred while processing the files.")

            try:
                with open(output_path, "rb") as output_file:
                    pdf_bytes = output_file.read(MAX_OUTPUT_SIZE_BYTES + 1)
            except OSError:
                logger.exception("Failed to read output file")
                return error_response(500, "An error occurred while processing the files.")

            if not pdf_bytes or len(pdf_bytes) > MAX_OUTPUT_SIZE_BYTES:
                return error_response(500, "An error occurred while processing the files.")

            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": 'attachment; filename="concatenated.pdf"'
                },
            )

    except Exception:
        logger.exception("Unhandled error while processing concatenate request")
        return error_response(500, "An error occurred while processing the files.")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)