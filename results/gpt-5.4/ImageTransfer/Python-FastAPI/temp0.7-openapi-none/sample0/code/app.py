import imghdr
import mimetypes
import os
import secrets
import traceback
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
import uvicorn


APP_TITLE = "Image Upload and Share API"
APP_DESCRIPTION = "An API for uploading images and generating shareable links."
APP_VERSION = "1.0.0"

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title=APP_TITLE,
    description=APP_DESCRIPTION,
    version=APP_VERSION,
)


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def detect_image_mime(file_path: Path) -> str | None:
    detected = imghdr.what(file_path)
    if detected == "jpeg":
        return "image/jpeg"
    if detected == "png":
        return "image/png"
    if detected == "gif":
        return "image/gif"
    if detected == "bmp":
        return "image/bmp"
    if detected == "webp":
        return "image/webp"
    if detected == "tiff":
        return "image/tiff"

    guessed, _ = mimetypes.guess_type(file_path.name)
    if guessed and guessed.startswith("image/"):
        return guessed

    return None


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    traceback.print_exc()
    return error_response("Internal server error", 500)


@app.post(
    "/upload",
    responses={
        200: {
            "description": "Successful upload",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "The identifier of the uploaded image.",
                            }
                        },
                        "required": ["id"],
                    }
                }
            },
        },
        400: {
            "description": "Bad request, invalid input",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"error": {"type": "string"}},
                        "required": ["error"],
                    }
                }
            },
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"error": {"type": "string"}},
                        "required": ["error"],
                    }
                }
            },
        },
    },
    summary="Upload an image. Accept all formats.",
    description="Allow users to upload images, returning a shareable link.",
)
async def upload_image(file: UploadFile = File(...)):
    if file is None:
        return error_response("No file provided", 400)

    original_name = file.filename or ""
    extension = Path(original_name).suffix
    image_id = secrets.token_urlsafe(16)
    stored_filename = f"{image_id}{extension}"
    file_path = UPLOAD_DIR / stored_filename

    try:
        content = await file.read()
    finally:
        await file.close()

    if not content:
        return error_response("Uploaded file is empty", 400)

    try:
        with open(file_path, "wb") as out_file:
            out_file.write(content)
    except OSError:
        return error_response("Internal server error", 500)

    mime_type = detect_image_mime(file_path)
    if mime_type is None:
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            pass
        return error_response("Invalid input", 400)

    return {"id": image_id}


@app.get(
    "/images/{imageId}",
    responses={
        200: {
            "description": "The image file being viewed. Mimetype of original image is be preserved.",
            "content": {"*/*": {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {
            "description": "Image not found",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"error": {"type": "string"}},
                        "required": ["error"],
                    }
                }
            },
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"error": {"type": "string"}},
                        "required": ["error"],
                    }
                }
            },
        },
    },
    summary="Share an image",
    description="Look at an image in the browser.",
)
async def get_image(imageId: str):
    try:
        matches = [path for path in UPLOAD_DIR.iterdir() if path.is_file() and path.stem == imageId]
    except OSError:
        return error_response("Internal server error", 500)

    if not matches:
        return error_response("Image not found", 404)

    file_path = matches[0]
    mime_type = detect_image_mime(file_path)
    if mime_type is None:
        return error_response("Image not found", 404)

    headers = {
        "Content-Disposition": f'inline; filename="{file_path.name}"',
        "X-Content-Type-Options": "nosniff",
    }

    return FileResponse(
        path=file_path,
        media_type=mime_type,
        filename=file_path.name,
        content_disposition_type="inline",
        headers=headers,
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)