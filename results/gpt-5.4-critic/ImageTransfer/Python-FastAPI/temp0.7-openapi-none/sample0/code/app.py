import json
import mimetypes
import secrets
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MiB
CHUNK_SIZE = 1024 * 1024  # 1 MiB
ID_LENGTH = 32
ID_ALPHABET = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


class UploadSuccessResponse(BaseModel):
    id: str


class ErrorResponse(BaseModel):
    error: str


app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)


def _safe_extension(filename: str | None) -> str:
    if not filename:
        return ""
    suffix = Path(filename).suffix
    if not suffix:
        return ""
    if len(suffix) > 20:
        return ""
    if any(char in suffix for char in ("/", "\\", "\x00")):
        return ""
    return suffix.lower()


def _build_image_path(image_id: str, extension: str) -> Path:
    return UPLOAD_DIR / f"{image_id}{extension}"


def _metadata_path(image_id: str) -> Path:
    return UPLOAD_DIR / f"{image_id}.meta.json"


def _is_valid_image_id(image_id: str) -> bool:
    return len(image_id) == ID_LENGTH and all(char in ID_ALPHABET for char in image_id)


def _store_metadata(image_id: str, original_content_type: str | None, extension: str) -> None:
    metadata = {
        "content_type": original_content_type or "application/octet-stream",
        "extension": extension,
    }
    metadata_file = _metadata_path(image_id)
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f)


def _load_metadata(image_id: str) -> dict | None:
    metadata_file = _metadata_path(image_id)
    if not metadata_file.is_file():
        return None
    try:
        with open(metadata_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def _find_image_path(image_id: str) -> Path | None:
    metadata = _load_metadata(image_id)
    if metadata is not None:
        extension = metadata.get("extension", "")
        if isinstance(extension, str):
            candidate = _build_image_path(image_id, extension)
            if candidate.is_file():
                return candidate

    for candidate in UPLOAD_DIR.iterdir():
        if candidate.is_file() and candidate.stem == image_id and not candidate.name.endswith(".meta.json"):
            return candidate
    return None


def _guess_content_type_from_name(filename: str | None) -> str:
    guessed, _ = mimetypes.guess_type(filename or "")
    if guessed:
        return guessed
    return "application/octet-stream"


@app.post(
    "/upload",
    response_model=UploadSuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request, invalid input"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
    summary="Upload an image. Accept all formats.",
    description="Allow users to upload images, returning a shareable link.",
)
async def upload_image(request: Request, file: UploadFile = File(...)) -> UploadSuccessResponse:
    image_id = secrets.token_urlsafe(24)
    extension = _safe_extension(file.filename)
    image_path = _build_image_path(image_id, extension)
    total_size = 0
    wrote_any_data = False

    try:
        with open(image_path, "wb") as out_file:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                wrote_any_data = True
                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE:
                    out_file.close()
                    try:
                        image_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return JSONResponse(status_code=400, content={"error": "Uploaded file is too large"})
                out_file.write(chunk)

        if not wrote_any_data:
            try:
                image_path.unlink(missing_ok=True)
            except Exception:
                pass
            return JSONResponse(status_code=400, content={"error": "Uploaded file is empty"})

        _store_metadata(image_id, file.content_type, extension)

        request.base_url
        return UploadSuccessResponse(id=image_id)
    except Exception:
        try:
            image_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            _metadata_path(image_id).unlink(missing_ok=True)
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"error": "Internal server error"})
    finally:
        await file.close()


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
async def get_image(imageId: str):
    try:
        if not _is_valid_image_id(imageId):
            return JSONResponse(status_code=404, content={"error": "Image not found"})

        image_path = _find_image_path(imageId)
        if image_path is None or not image_path.is_file():
            return JSONResponse(status_code=404, content={"error": "Image not found"})

        file_size = image_path.stat().st_size
        if file_size > MAX_UPLOAD_SIZE:
            return JSONResponse(status_code=500, content={"error": "Internal server error"})

        with open(image_path, "rb") as f:
            file_bytes = f.read()

        metadata = _load_metadata(imageId) or {}
        media_type = metadata.get("content_type")
        if not isinstance(media_type, str) or not media_type:
            media_type = _guess_content_type_from_name(image_path.name)

        headers = {
            "Content-Disposition": f'inline; filename="{image_path.name}"',
            "X-Content-Type-Options": "nosniff",
        }

        return Response(content=file_bytes, media_type=media_type, headers=headers)
    except Exception:
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)