import json
import mimetypes
import os
import secrets
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, Path as FastAPIPath, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploaded_images"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MiB
CHUNK_SIZE = 1024 * 1024  # 1 MiB

app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)


class UploadSuccessResponse(BaseModel):
    id: str


class ErrorResponse(BaseModel):
    error: str


def _safe_extension(filename: str | None) -> str:
    if not filename:
        return ""
    suffix = Path(filename).suffix
    if not suffix:
        return ""
    safe = "".join(ch for ch in suffix[:20] if ch.isalnum() or ch == ".")
    return safe if safe.startswith(".") else ""


def _build_storage_path(image_id: str, original_filename: str | None) -> Path:
    extension = _safe_extension(original_filename)
    return UPLOAD_DIR / f"{image_id}{extension}"


def _meta_path(image_id: str) -> Path:
    return UPLOAD_DIR / f"{image_id}.meta.json"


def _find_image_file(image_id: str) -> Path | None:
    exact = UPLOAD_DIR / image_id
    if exact.is_file() and exact.name != f"{image_id}.meta.json":
        return exact

    matches = []
    for path in UPLOAD_DIR.glob(f"{image_id}.*"):
        if path.is_file() and path.name != f"{image_id}.meta.json":
            matches.append(path)

    if matches:
        matches.sort()
        return matches[0]

    return None


def _is_png(header: bytes) -> bool:
    return header.startswith(b"\x89PNG\r\n\x1a\n")


def _is_jpeg(header: bytes) -> bool:
    return len(header) >= 3 and header[:3] == b"\xff\xd8\xff"


def _is_gif(header: bytes) -> bool:
    return header.startswith(b"GIF87a") or header.startswith(b"GIF89a")


def _is_bmp(header: bytes) -> bool:
    return header.startswith(b"BM")


def _is_webp(header: bytes) -> bool:
    return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"


def _is_tiff(header: bytes) -> bool:
    return header.startswith(b"II*\x00") or header.startswith(b"MM\x00*")


def _is_ico(header: bytes) -> bool:
    return len(header) >= 4 and header[:4] == b"\x00\x00\x01\x00"


def _is_heic_like(header: bytes) -> bool:
    if len(header) < 12:
        return False
    if header[4:8] != b"ftyp":
        return False
    brand = header[8:12]
    return brand in {
        b"heic",
        b"heix",
        b"hevc",
        b"hevx",
        b"mif1",
        b"msf1",
        b"avif",
    }


def _sniff_image_mime(header: bytes) -> str | None:
    if _is_png(header):
        return "image/png"
    if _is_jpeg(header):
        return "image/jpeg"
    if _is_gif(header):
        return "image/gif"
    if _is_bmp(header):
        return "image/bmp"
    if _is_webp(header):
        return "image/webp"
    if _is_tiff(header):
        return "image/tiff"
    if _is_ico(header):
        return "image/x-icon"
    if _is_heic_like(header):
        brand = header[8:12]
        if brand == b"avif":
            return "image/avif"
        return "image/heic"
    return None


def _normalize_image_content_type(content_type: str | None) -> str | None:
    if not content_type:
        return None
    normalized = content_type.split(";")[0].strip().lower()
    if normalized.startswith("image/"):
        return normalized
    return None


def _determine_upload_mime_type(initial_bytes: bytes, declared_content_type: str | None, filename: str | None) -> str:
    sniffed = _sniff_image_mime(initial_bytes)
    if sniffed:
        return sniffed

    declared = _normalize_image_content_type(declared_content_type)
    if declared:
        return declared

    guessed, _ = mimetypes.guess_type(filename or "")
    if guessed:
        guessed = guessed.lower()
        if guessed.startswith("image/"):
            return guessed

    raise HTTPException(status_code=400, detail="Uploaded file is not a valid image")


def _write_metadata(image_id: str, media_type: str) -> None:
    metadata = {"media_type": media_type}
    with open(_meta_path(image_id), "w", encoding="utf-8") as meta_file:
        json.dump(metadata, meta_file)


def _read_metadata_media_type(image_id: str) -> str | None:
    path = _meta_path(image_id)
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as meta_file:
            data = json.load(meta_file)
    except (OSError, ValueError, TypeError):
        return None

    media_type = data.get("media_type")
    if isinstance(media_type, str) and media_type.startswith("image/"):
        return media_type
    return None


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    if isinstance(exc.detail, str):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
    return JSONResponse(status_code=exc.status_code, content={"error": "Request failed"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, __: Exception):
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


@app.post(
    "/upload",
    response_model=UploadSuccessResponse,
    responses={
        200: {"description": "Successful upload"},
        400: {"model": ErrorResponse, "description": "Bad request, invalid input"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="Upload an image. Accept all formats.",
    description="Allow users to upload images, returning a shareable link.",
)
async def upload_image(file: UploadFile = File(...)):
    if file is None:
        raise HTTPException(status_code=400, detail="No file provided")

    image_id = secrets.token_urlsafe(16)
    storage_path = _build_storage_path(image_id, file.filename)

    total_size = 0
    initial_bytes = b""
    first_chunk = True

    try:
        with open(storage_path, "wb") as output_file:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break

                if first_chunk:
                    initial_bytes = chunk[:512]
                    first_chunk = False

                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE:
                    raise HTTPException(status_code=400, detail="Uploaded file is too large")

                output_file.write(chunk)

        if total_size == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        media_type = _determine_upload_mime_type(initial_bytes, file.content_type, file.filename)
        _write_metadata(image_id, media_type)

    except HTTPException:
        try:
            if storage_path.exists():
                storage_path.unlink()
            meta = _meta_path(image_id)
            if meta.exists():
                meta.unlink()
        except OSError:
            pass
        raise
    except OSError:
        try:
            if storage_path.exists():
                storage_path.unlink()
            meta = _meta_path(image_id)
            if meta.exists():
                meta.unlink()
        except OSError:
            pass
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        await file.close()

    return UploadSuccessResponse(id=image_id)


@app.get(
    "/images/{imageId}",
    responses={
        200: {
            "description": "The image file being viewed. Mimetype of original image is be preserved.",
            "content": {"*/*": {"schema": {"type": "string", "format": "binary"}}},
        },
        404: {"model": ErrorResponse, "description": "Image not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="Share an image",
    description="Look at an image in the browser.",
)
async def get_image(imageId: str = FastAPIPath(..., description="The ID of the image to be viewed.")):
    image_path = _find_image_file(imageId)
    if image_path is None or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")

    try:
        data = image_path.read_bytes()
    except OSError:
        raise HTTPException(status_code=500, detail="Internal server error")

    media_type = _read_metadata_media_type(imageId)
    if not media_type:
        guessed, _ = mimetypes.guess_type(image_path.name)
        if guessed and guessed.startswith("image/"):
            media_type = guessed
        else:
            media_type = "application/octet-stream"

    headers = {
        "Content-Disposition": f'inline; filename="{image_path.name}"',
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=data, media_type=media_type, headers=headers)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)