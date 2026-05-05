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

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MiB
READ_CHUNK_SIZE = 1024 * 1024  # 1 MiB

app = FastAPI(
    title=APP_TITLE,
    description=APP_DESCRIPTION,
    version=APP_VERSION,
)


def error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


def detect_image_type(data: bytes) -> tuple[str, str] | None:
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return ".jpg", "image/jpeg"

    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png", "image/png"

    if len(data) >= 6 and (data[:6] == b"GIF87a" or data[:6] == b"GIF89a"):
        return ".gif", "image/gif"

    if len(data) >= 2 and data[:2] == b"BM":
        return ".bmp", "image/bmp"

    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"

    if len(data) >= 4:
        if data[:4] == b"II*\x00" or data[:4] == b"MM\x00*":
            return ".tif", "image/tiff"

    if len(data) >= 8:
        if data[:8] == b"\x89JXL\r\n\x1a\n":
            return ".jxl", "image/jxl"

    if len(data) >= 12:
        if data[4:8] == b"ftyp":
            brand = data[8:12]
            if brand in {
                b"avif",
                b"avis",
                b"mif1",
                b"msf1",
                b"heic",
                b"heix",
                b"hevc",
                b"hevx",
            }:
                if brand in {b"avif", b"avis"}:
                    return ".avif", "image/avif"
                return ".heic", "image/heic"

    return None


def find_image_path(image_id: str) -> Path | None:
    for path in UPLOAD_DIR.iterdir():
        if not path.is_file():
            continue
        if path.suffix == ".meta":
            continue
        if path.stem == image_id:
            return path
    return None


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
    image_path = None
    meta_path = None
    try:
        if file is None:
            return error_response(400, "Missing file")

        chunks: list[bytes] = []
        total_size = 0

        while True:
            chunk = await file.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > MAX_UPLOAD_SIZE:
                return error_response(400, "Uploaded file is too large")
            chunks.append(chunk)

        data = b"".join(chunks)

        if not data:
            return error_response(400, "Uploaded file is empty")

        detected = detect_image_type(data)
        if detected is None:
            return error_response(400, "Invalid image file")

        extension, mime_type = detected
        image_id = secrets.token_urlsafe(16)

        image_path = UPLOAD_DIR / f"{image_id}{extension}"
        meta_path = UPLOAD_DIR / f"{image_id}.meta"

        with open(image_path, "wb") as image_file:
            image_file.write(data)

        with open(meta_path, "w", encoding="utf-8") as meta_file:
            meta_file.write(mime_type)

        return {"id": image_id}
    except Exception:
        if image_path is not None and image_path.exists():
            try:
                image_path.unlink()
            except OSError:
                pass
        if meta_path is not None and meta_path.exists():
            try:
                meta_path.unlink()
            except OSError:
                pass
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
        image_path = find_image_path(imageId)
        if image_path is None:
            return error_response(404, "Image not found")

        meta_path = UPLOAD_DIR / f"{imageId}.meta"
        if not meta_path.exists():
            return error_response(404, "Image not found")

        mime_type = meta_path.read_text(encoding="utf-8").strip()
        if not mime_type.startswith("image/"):
            return error_response(404, "Image not found")

        with open(image_path, "rb") as image_file:
            data = image_file.read()

        if detect_image_type(data) is None:
            return error_response(404, "Image not found")

        headers = {
            "Content-Disposition": f'inline; filename="image{image_path.suffix}"',
            "X-Content-Type-Options": "nosniff",
        }
        return Response(content=data, media_type=mime_type, headers=headers)
    except Exception:
        return error_response(500, "Internal server error")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)