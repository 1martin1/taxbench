import os
import shutil
import subprocess
import tempfile
from typing import List

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, Response
import uvicorn

app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
)


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


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

    if shutil.which("pdfunite") is None:
        return error_response(500, "An error occurred while processing the files.")

    for uploaded_file in files:
        if uploaded_file is None or not uploaded_file.filename:
            return error_response(400, "Invalid input or missing files.")
        content_type = (uploaded_file.content_type or "").lower()
        filename = uploaded_file.filename.lower()
        if content_type != "application/pdf" and not filename.endswith(".pdf"):
            return error_response(400, "Invalid input or missing files.")

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_paths = []

            for index, uploaded_file in enumerate(files):
                input_path = os.path.join(temp_dir, f"input_{index}.pdf")
                with open(input_path, "wb") as destination:
                    while True:
                        chunk = await uploaded_file.read(1024 * 1024)
                        if not chunk:
                            break
                        destination.write(chunk)
                await uploaded_file.close()

                if os.path.getsize(input_path) == 0:
                    return error_response(400, "Invalid input or missing files.")

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
                return error_response(500, "An error occurred while processing the files.")

            with open(output_path, "rb") as output_file:
                pdf_bytes = output_file.read()

            if not pdf_bytes:
                return error_response(500, "An error occurred while processing the files.")

            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": 'attachment; filename="concatenated.pdf"'
                },
            )

    except Exception:
        return error_response(500, "An error occurred while processing the files.")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)