import os
import shutil
import subprocess
import tempfile
from typing import List

from fastapi import FastAPI, File, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
import uvicorn


MAX_FILES = 20
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MiB per file
MAX_TOTAL_SIZE = 100 * 1024 * 1024  # 100 MiB total
PDF_HEADER = b"%PDF-"


app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
)


class ClientInputError(Exception):
    pass


class ServerProcessingError(Exception):
    pass


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def _validate_pdf_header(file_path: str) -> bool:
    try:
        with open(file_path, "rb") as f:
            header = f.read(len(PDF_HEADER))
        return header == PDF_HEADER
    except OSError:
        return False


def _write_upload_to_disk(upload_file, destination_path: str) -> int:
    total_written = 0
    try:
        upload_file.file.seek(0)
    except Exception:
        pass

    try:
        with open(destination_path, "wb") as out_file:
            while True:
                chunk = upload_file.file.read(1024 * 1024)
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > MAX_FILE_SIZE:
                    raise ClientInputError("Invalid input or missing files.")
                out_file.write(chunk)
    except ClientInputError:
        raise
    except OSError as exc:
        raise ServerProcessingError("An error occurred while processing the files.") from exc

    if total_written <= 0:
        raise ClientInputError("Invalid input or missing files.")

    return total_written


def _run_pdfunite(command: List[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise ServerProcessingError("An error occurred while processing the files.") from exc


def _read_output_file(file_path: str) -> bytes:
    try:
        with open(file_path, "rb") as f:
            return f.read()
    except OSError as exc:
        raise ServerProcessingError("An error occurred while processing the files.") from exc


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
        return error_response("Invalid input or missing files.", 400)

    if len(files) > MAX_FILES:
        return error_response("Invalid input or missing files.", 400)

    if shutil.which("pdfunite") is None:
        return error_response("An error occurred while processing the files.", 500)

    total_size = 0

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_paths = []

            for index, upload in enumerate(files):
                if not upload.filename:
                    raise ClientInputError("Invalid input or missing files.")

                content_type = (upload.content_type or "").lower()
                if content_type and content_type != "application/pdf":
                    raise ClientInputError("Invalid input or missing files.")

                input_path = os.path.join(temp_dir, f"input_{index}.pdf")
                file_size = await run_in_threadpool(_write_upload_to_disk, upload, input_path)
                total_size += file_size

                if total_size > MAX_TOTAL_SIZE:
                    raise ClientInputError("Invalid input or missing files.")

                is_pdf = await run_in_threadpool(_validate_pdf_header, input_path)
                if not is_pdf:
                    raise ClientInputError("Invalid input or missing files.")

                input_paths.append(input_path)

            if not input_paths:
                raise ClientInputError("Invalid input or missing files.")

            output_path = os.path.join(temp_dir, "concatenated.pdf")
            command = ["pdfunite", *input_paths, output_path]

            result = await run_in_threadpool(_run_pdfunite, command)

            if result.returncode != 0 or not os.path.exists(output_path):
                stderr_text = (result.stderr or b"").lower()
                if b"pdf" in stderr_text or b"syntax" in stderr_text or b"format" in stderr_text:
                    raise ClientInputError("Invalid input or missing files.")
                raise ServerProcessingError("An error occurred while processing the files.")

            output_bytes = await run_in_threadpool(_read_output_file, output_path)

            if not output_bytes or not output_bytes.startswith(PDF_HEADER):
                raise ServerProcessingError("An error occurred while processing the files.")

            return Response(
                content=output_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": 'attachment; filename="concatenated.pdf"'},
            )

    except ClientInputError as exc:
        return error_response(str(exc), 400)
    except ServerProcessingError as exc:
        return error_response(str(exc), 500)
    finally:
        for upload in files:
            try:
                await upload.close()
            except Exception:
                pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)