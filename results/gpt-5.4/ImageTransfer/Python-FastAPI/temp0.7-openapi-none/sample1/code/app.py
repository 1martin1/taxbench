import imghdr
import mimetypes
import os
import secrets
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, Response

APP_TITLE = "Image Upload and Share API"
APP_DESCRIPTION = "An API for uploading images and generating shareable links."
APP_VERSION = "1.0.0"

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploaded_images"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title=APP_TITLE,
    description=APP_DESCRIPTION,
    version=APP_VERSION,
)


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def detect_extension_and_mime(data: bytes, original_filename: str | None, content_type: str | None) -> tuple[str, str]:
    detected = imghdr.what(None, h=data)
    if detected == "jpeg":
        return ".jpg", "image/jpeg"
    if detected == "png":
        return ".png", "image/png"
    if detected == "gif":
        return ".gif", "image/gif"
    if detected == "bmp":
        return ".bmp", "image/bmp"
    if detected == "webp":
        return ".webp", "image/webp"
    if detected == "tiff":
        return ".tif", "image/tiff"
    if detected == "rgb":
        return ".rgb", "image/x-rgb"
    if detected == "pbm":
        return ".pbm", "image/x-portable-bitmap"
    if detected == "pgm":
        return ".pgm", "image/x-portable-graymap"
    if detected == "ppm":
        return ".ppm", "image/x-portable-pixmap"
    if detected == "rast":
        return ".rast", "image/cmu-raster"
    if detected == "xbm":
        return ".xbm", "image/x-xbitmap"

    suffix = ""
    if original_filename:
        suffix = Path(original_filename).suffix.lower()

    mime = ""
    if suffix:
        mime = mimetypes.types_map.get(suffix, "")

    if not mime and content_type and "/" in content_type:
        mime = content_type

    if not suffix:
        guessed_ext = mimetypes.guess_extension(mime) if mime else None
        if guessed_ext:
            suffix = guessed_ext.lower()

    if not mime:
        mime = "application/octet-stream"

    return suffix, mime


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
                    }
                }
            },
        },
    },
    summary="Upload an image. Accept all formats.",
    description="Allow users to upload images, returning a shareable link.",
)
async def upload_image(file: UploadFile = File(...)):
    try:
        if file is None:
            return error_response(400, "Missing file")

        data = await file.read()
        if not data:
            return error_response(400, "Uploaded file is empty")

        image_id = secrets.token_urlsafe(16)
        extension, mime_type = detect_extension_and_mime(data, file.filename, file.content_type)

        image_path = UPLOAD_DIR / f"{image_id}{extension}"
        meta_path = UPLOAD_DIR / f"{image_id}.meta"

        with open(image_path, "wb") as image_file:
            image_file.write(data)

        with open(meta_path, "w", encoding="utf-8") as meta_file:
            meta_file.write(mime_type)

        return {"id": image_id}
    except Exception:
        return error_response(500, "Internal server error")
    finally:
        if file is not None:
            await file.close()


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
        candidates = [p for p in UPLOAD_DIR.glob(f"{imageId}*") if p.is_file() and p.suffix != ".meta"]

        if not candidates:
            return error_response(404, "Image not found")

        image_path = candidates[0]
        meta_path = UPLOAD_DIR / f"{imageId}.meta"

        if meta_path.exists():
            mime_type = meta_path.read_text(encoding="utf-8").strip() or "application/octet-stream"
        else:
            mime_type, _ = mimetypes.guess_type(str(image_path))
            mime_type = mime_type or "application/octet-stream"

        with open(image_path, "rb") as image_file:
            data = image_file.read()

        headers = {
            "Content-Disposition": 'inline; filename="image%s"' % image_path.suffix,
            "X-Content-Type-Options": "nosniff",
        }
        return Response(content=data, media_type=mime_type, headers=headers)
    except Exception:
        return error_response(500, "Internal server error")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)