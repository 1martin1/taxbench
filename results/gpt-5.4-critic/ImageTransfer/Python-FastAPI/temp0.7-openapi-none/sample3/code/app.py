import os
import re
import secrets
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "uploaded_images"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MiB
CHUNK_SIZE = 1024 * 1024  # 1 MiB
IMAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)


class UploadSuccessResponse(BaseModel):
    id: str


class ErrorResponse(BaseModel):
    error: str


def _validate_image_id(image_id: str) -> str:
    if not image_id or not IMAGE_ID_PATTERN.fullmatch(image_id):
        raise HTTPException(status_code=404, detail="Image not found")
    return image_id


def _safe_image_path(image_id: str) -> Path:
    safe_id = _validate_image_id(image_id)
    return STORAGE_DIR / safe_id


def _sanitize_content_type(content_type: str | None) -> str:
    if not content_type:
        return "application/octet-stream"
    value = content_type.strip().split(";", 1)[0].strip().lower()
    if not value or any(ch in value for ch in ("\r", "\n", "\x00")):
        return "application/octet-stream"
    if "/" not in value:
        return "application/octet-stream"
    return value


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    missing_file = False
    for error in exc.errors():
        loc = error.get("loc", ())
        if len(loc) >= 2 and loc[0] == "body" and loc[-1] == "file":
            missing_file = True
            break

    if missing_file:
        return JSONResponse(status_code=400, content={"error": "No file provided"})

    return JSONResponse(status_code=400, content={"error": "Invalid input"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, __: Exception):
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


@app.post(
    "/upload",
    response_model=UploadSuccessResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Upload an image. Accept all formats.",
    description="Allow users to upload images, returning a shareable link.",
)
async def upload_image(file: UploadFile = File(...)):
    image_id = secrets.token_urlsafe(16)
    image_path = _safe_image_path(image_id)
    meta_path = _safe_image_path(f"{image_id}_meta")

    total_size = 0
    try:
        with open(image_path, "wb") as out_file:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break

                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE:
                    try:
                        out_file.close()
                    finally:
                        if image_path.exists():
                            image_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=400, detail="Uploaded file is too large")

                out_file.write(chunk)

        if total_size == 0:
            if image_path.exists():
                image_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        content_type = _sanitize_content_type(file.content_type)
        meta_path.write_text(content_type, encoding="utf-8")
    except HTTPException:
        raise
    except Exception:
        if image_path.exists():
            image_path.unlink(missing_ok=True)
        if meta_path.exists():
            meta_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        await file.close()

    return UploadSuccessResponse(id=image_id)


@app.get(
    "/images/{imageId}",
    responses={
        200: {
            "content": {
                "*/*": {
                    "schema": {
                        "type": "string",
                        "format": "binary",
                    }
                }
            }
        },
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Share an image",
    description="Look at an image in the browser.",
)
async def get_image(imageId: str):
    image_path = _safe_image_path(imageId)
    meta_path = _safe_image_path(f"{imageId}_meta")

    if not image_path.is_file() or not meta_path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")

    try:
        file_size = image_path.stat().st_size
        if file_size > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=500, detail="Internal server error")

        with open(image_path, "rb") as image_file:
            content = image_file.read(MAX_UPLOAD_SIZE + 1)

        if len(content) != file_size or len(content) > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=500, detail="Internal server error")

        content_type = _sanitize_content_type(meta_path.read_text(encoding="utf-8"))
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Image not found")
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")

    headers = {
        "Content-Disposition": "inline",
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=content, media_type=content_type, headers=headers)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)