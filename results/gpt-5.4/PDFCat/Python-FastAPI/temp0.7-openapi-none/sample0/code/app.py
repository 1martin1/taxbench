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


def error_response(message: str, status_code: int) -> JSONResponse:
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
        return error_response("Invalid input or missing files.", 400)

    if shutil.which("pdfunite") is None:
        return error_response("An error occurred while processing the files.", 500)

    with tempfile.TemporaryDirectory() as temp_dir:
        input_paths = []

        try:
            for index, upload in enumerate(files):
                if not upload.filename:
                    return error_response("Invalid input or missing files.", 400)

                content_type = (upload.content_type or "").lower()
                if content_type and content_type != "application/pdf":
                    return error_response("Invalid input or missing files.", 400)

                file_bytes = await upload.read()
                if not file_bytes:
                    return error_response("Invalid input or missing files.", 400)

                input_path = os.path.join(temp_dir, f"input_{index}.pdf")
                with open(input_path, "wb") as f:
                    f.write(file_bytes)
                input_paths.append(input_path)

            if not input_paths:
                return error_response("Invalid input or missing files.", 400)

            output_path = os.path.join(temp_dir, "concatenated.pdf")
            command = ["pdfunite", *input_paths, output_path]

            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            if result.returncode != 0 or not os.path.exists(output_path):
                return error_response("An error occurred while processing the files.", 500)

            with open(output_path, "rb") as f:
                output_bytes = f.read()

            return Response(
                content=output_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": 'attachment; filename="concatenated.pdf"'},
            )

        except Exception:
            return error_response("An error occurred while processing the files.", 500)
        finally:
            for upload in files:
                try:
                    await upload.close()
                except Exception:
                    pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)