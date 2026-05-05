import os
import subprocess
import tempfile
from typing import Set
import secrets

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response
import uvicorn

app = FastAPI()

MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100MB
ALLOWED_EXTENSIONS: Set[str] = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
MAX_FRAME_NUMBER = 1000000
FFMPEG_TIMEOUT = 30


def _validate_file(upload: UploadFile, filename: str) -> str:
    if upload.content_type and not upload.content_type.startswith("video/"):
        raise ValueError("Invalid content type")

    _, ext = os.path.splitext(filename)
    ext = ext.lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Invalid file extension")

    return ext


def _validate_frame_number(frame_number: int):
    if frame_number < 0 or frame_number > MAX_FRAME_NUMBER:
        raise ValueError("Invalid frame number")


def _safe_filename(prefix: str = "upload", suffix: str = "") -> str:
    random_token = secrets.token_hex(16)
    return f"{prefix}_{random_token}{suffix}"


@app.post("/extract")
async def extract_frame(
    video: UploadFile = File(...),
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
):
    try:
        _validate_frame_number(frameNumber)
        ext = _validate_file(video, videoFileName)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    temp_video_path = None
    output_path = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=ext,
            prefix="video_"
        ) as tmp:
            temp_video_path = tmp.name
            total = 0

            while True:
                chunk = await video.read(1024 * 1024)
                if not chunk:
                    break

                total += len(chunk)
                if total > MAX_UPLOAD_SIZE:
                    return JSONResponse(
                        status_code=413,
                        content={"error": "File too large"},
                    )

                tmp.write(chunk)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as out:
            output_path = out.name

        cmd = [
            "ffmpeg",
            "-i",
            temp_video_path,
            "-vf",
            f"select=eq(n\\,{frameNumber})",
            "-vframes",
            "1",
            output_path,
            "-y",
        ]

        try:
            subprocess.run(
                cmd,
                check=True,
                timeout=FFMPEG_TIMEOUT,
                capture_output=True
            )
        except subprocess.CalledProcessError:
            return JSONResponse(
                status_code=404,
                content={"error": f"Frame at index {frameNumber} could not be found."},
            )
        except subprocess.TimeoutExpired:
            return JSONResponse(
                status_code=504,
                content={"error": "Processing timeout"},
            )

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return JSONResponse(
                status_code=404,
                content={"error": f"Frame at index {frameNumber} could not be found."},
            )

        with open(output_path, "rb") as f:
            content = f.read()

        return Response(
            content=content,
            media_type="image/png",
            headers={
                "Content-Type": "image/png",
                "X-Content-Type-Options": "nosniff"
            }
        )

    finally:
        try:
            await video.close()
        except Exception:
            pass

        for path in (temp_video_path, output_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {"message": "FrameExtract API", "docs": "/docs"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)