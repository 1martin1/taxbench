import io
import os
import zipfile
from typing import List

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
import uvicorn


app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)


MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MiB
MAX_ENTRIES = 1000
MAX_ENTRY_UNCOMPRESSED_SIZE = 2 * 1024 * 1024  # 2 MiB
MAX_TOTAL_UNCOMPRESSED_SIZE = 20 * 1024 * 1024  # 20 MiB
MAX_RESPONSE_SIZE = 20 * 1024 * 1024  # 20 MiB
MAX_COMPRESSION_RATIO = 1000

ALLOWED_CONTENT_TYPES = {
    "application/zip",
    "application/x-zip-compressed",
    "multipart/x-zip",
    "application/octet-stream",
}


def is_text_file(filename: str) -> bool:
    return filename.lower().endswith(".txt")


def decode_text_file(data: bytes) -> str:
    encodings_to_try: List[str] = ["utf-8", "utf-8-sig", "latin-1"]
    for encoding in encodings_to_try:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Unable to decode text file contents.")


def is_safe_archive_path(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if not normalized or normalized.startswith("/") or normalized.startswith("../"):
        return False
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    return all(part != ".." for part in parts)


@app.post(
    "/convert",
    summary="Convert a zip file to a text file",
    description="Convert a zip file into a single text file, concatenating the contents of all text files in the zip.",
    response_class=PlainTextResponse,
    responses={
        200: {
            "description": "Conversion successful",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        400: {"description": "Invalid input"},
    },
)
async def convert(file: UploadFile = File(...)) -> PlainTextResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Invalid zip file.")

    if file.content_type and file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid zip file.")

    uploaded_bytes = await file.read(MAX_UPLOAD_SIZE + 1)
    await file.close()

    if not uploaded_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(uploaded_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="Invalid zip file.")

    try:
        zip_buffer = io.BytesIO(uploaded_bytes)
        with zipfile.ZipFile(zip_buffer, "r") as archive:
            try:
                file_infos = archive.infolist()
            except Exception as exc:
                raise HTTPException(status_code=400, detail="Invalid zip file.") from exc

            if len(file_infos) > MAX_ENTRIES:
                raise HTTPException(status_code=400, detail="Invalid zip file.")

            non_dir_infos = [info for info in file_infos if not info.is_dir()]

            total_uncompressed_size = 0
            text_file_infos = []

            for info in non_dir_infos:
                if not is_safe_archive_path(info.filename):
                    raise HTTPException(status_code=400, detail="Invalid zip file.")

                if info.file_size < 0 or info.compress_size < 0:
                    raise HTTPException(status_code=400, detail="Invalid zip file.")

                if info.file_size > MAX_ENTRY_UNCOMPRESSED_SIZE:
                    raise HTTPException(status_code=400, detail="Invalid zip file.")

                total_uncompressed_size += info.file_size
                if total_uncompressed_size > MAX_TOTAL_UNCOMPRESSED_SIZE:
                    raise HTTPException(status_code=400, detail="Invalid zip file.")

                if info.compress_size == 0:
                    if info.file_size > 0:
                        raise HTTPException(status_code=400, detail="Invalid zip file.")
                else:
                    if info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                        raise HTTPException(status_code=400, detail="Invalid zip file.")

                if is_text_file(info.filename):
                    text_file_infos.append(info)

            if not text_file_infos:
                raise HTTPException(status_code=400, detail="No text files found in the zip archive.")

            concatenated_parts: List[str] = []
            current_response_size = 0

            for info in text_file_infos:
                try:
                    with archive.open(info, "r") as entry_file:
                        raw_content = entry_file.read(MAX_ENTRY_UNCOMPRESSED_SIZE + 1)
                except HTTPException:
                    raise
                except Exception as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Failed to read file '{info.filename}' from zip archive.",
                    ) from exc

                if len(raw_content) > MAX_ENTRY_UNCOMPRESSED_SIZE:
                    raise HTTPException(status_code=400, detail="Invalid zip file.")

                try:
                    text_content = decode_text_file(raw_content)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Failed to decode text file '{info.filename}'.",
                    ) from exc

                part = f"===== {info.filename} =====\n{text_content}"
                current_response_size += len(part.encode("utf-8"))
                if concatenated_parts:
                    current_response_size += len("\n\n".encode("utf-8"))

                if current_response_size > MAX_RESPONSE_SIZE:
                    raise HTTPException(status_code=400, detail="Invalid zip file.")

                concatenated_parts.append(part)

            result = "\n\n".join(concatenated_parts)
            return PlainTextResponse(content=result, status_code=200)

    except HTTPException:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid zip file.") from exc


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)