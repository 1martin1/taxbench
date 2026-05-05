import os
import shutil
import subprocess
import tempfile
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, Response
import uvicorn


PDF_CONTENT_TYPE = "application/pdf"


def create_error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def is_pdf_upload(upload: UploadFile) -> bool:
    filename = upload.filename or ""
    content_type = upload.content_type or ""
    return filename.lower().endswith(".pdf") or content_type == PDF_CONTENT_TYPE


@asynccontextmanager
async def lifespan(_: FastAPI):
    if shutil.which("pdfunite") is None:
        raise RuntimeError("pdfunite CLI is not installed or not available in PATH.")
    yield


app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
    lifespan=lifespan,
)


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
async def concatenate_pdfs(files: List[UploadFile] = File(...)):
    if not files:
        return create_error_response(400, "Invalid input or missing files.")

    for upload in files:
        if upload is None or not is_pdf_upload(upload):
            return create_error_response(400, "Invalid input or missing files.")

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_paths = []

            for index, upload in enumerate(files):
                input_path = os.path.join(temp_dir, f"input_{index}.pdf")
                file_bytes = await upload.read()

                if not file_bytes:
                    return create_error_response(400, "Invalid input or missing files.")

                with open(input_path, "wb") as f:
                    f.write(file_bytes)

                input_paths.append(input_path)

            output_path = os.path.join(temp_dir, "concatenated.pdf")
            command = ["pdfunite", *input_paths, output_path]

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0 or not os.path.exists(output_path):
                return create_error_response(
                    500, "An error occurred while processing the files."
                )

            with open(output_path, "rb") as f:
                output_bytes = f.read()

            return Response(
                content=output_bytes,
                media_type=PDF_CONTENT_TYPE,
                headers={
                    "Content-Disposition": 'attachment; filename="concatenated.pdf"'
                },
            )
    except Exception:
        return create_error_response(500, "An error occurred while processing the files.")
    finally:
        for upload in files:
            try:
                await upload.close()
            except Exception:
                pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)