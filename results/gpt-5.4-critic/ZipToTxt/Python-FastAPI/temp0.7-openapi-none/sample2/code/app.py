import asyncio
import io
import os
from typing import Optional
from zipfile import BadZipFile, ZipFile

from fastapi import FastAPI, File, Response, UploadFile
import uvicorn


app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)


TEXT_FILE_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".log",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".conf",
    ".py",
    ".js",
    ".ts",
    ".html",
    ".css",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".rs",
    ".go",
    ".sh",
    ".bat",
    ".sql",
}

MAX_UPLOAD_SIZE = 10 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1000
MAX_ENTRY_UNCOMPRESSED_SIZE = 2 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_SIZE = 20 * 1024 * 1024
READ_CHUNK_SIZE = 64 * 1024
MAX_CONCURRENT_CONVERSIONS = 4

_conversion_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CONVERSIONS)


def invalid_input_response() -> Response:
    return Response(status_code=400)


def is_text_file(filename: str) -> bool:
    lower_name = filename.lower()
    return any(lower_name.endswith(ext) for ext in TEXT_FILE_EXTENSIONS)


async def read_upload_with_limit(upload: UploadFile, limit: int) -> Optional[bytes]:
    chunks = []
    total_size = 0

    try:
        while True:
            chunk = await upload.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > limit:
                return None
            chunks.append(chunk)
    except Exception:
        return None

    return b"".join(chunks)


def validate_zip_signature(data: bytes) -> bool:
    if len(data) < 4:
        return False
    return data[:4] in (
        b"PK\x03\x04",
        b"PK\x05\x06",
        b"PK\x07\x08",
    )


def extract_texts_from_zip(zip_bytes: bytes) -> Optional[str]:
    if not validate_zip_signature(zip_bytes):
        return None

    try:
        with ZipFile(io.BytesIO(zip_bytes)) as zip_file:
            infos = zip_file.infolist()

            if len(infos) > MAX_ARCHIVE_ENTRIES:
                return None

            total_uncompressed_size = 0
            selected_infos = []

            for info in infos:
                if info.is_dir():
                    continue

                if info.file_size < 0 or info.compress_size < 0:
                    return None

                if info.file_size > MAX_ENTRY_UNCOMPRESSED_SIZE:
                    return None

                total_uncompressed_size += info.file_size
                if total_uncompressed_size > MAX_TOTAL_UNCOMPRESSED_SIZE:
                    return None

                if is_text_file(info.filename):
                    selected_infos.append(info)

            if not selected_infos:
                return ""

            selected_infos.sort(key=lambda item: item.filename)

            output = io.StringIO()
            first_written = False

            for info in selected_infos:
                decoded_chunks = []

                try:
                    with zip_file.open(info, "r") as extracted_file:
                        bytes_read = 0
                        while True:
                            chunk = extracted_file.read(READ_CHUNK_SIZE)
                            if not chunk:
                                break
                            bytes_read += len(chunk)
                            if bytes_read > MAX_ENTRY_UNCOMPRESSED_SIZE:
                                return None
                            decoded_chunks.append(chunk)

                    try:
                        content = b"".join(decoded_chunks).decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                except Exception:
                    return None

                if first_written:
                    output.write("\n")
                output.write(content)
                first_written = True

            return output.getvalue()
    except BadZipFile:
        return None
    except Exception:
        return None


@app.post(
    "/convert",
    summary="Convert a zip file to a text file",
    description="Convert a zip file into a single text file, concatenating the contents of all text files in the zip.",
    responses={
        200: {
            "description": "Conversion successful",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        400: {"description": "Invalid input"},
    },
)
async def convert(file: Optional[UploadFile] = File(None)) -> Response:
    if file is None:
        return invalid_input_response()

    filename = file.filename or ""
    if filename and not filename.lower().endswith(".zip"):
        return invalid_input_response()

    content_type = (file.content_type or "").lower()
    allowed_content_types = {
        "",
        "application/zip",
        "application/x-zip-compressed",
        "multipart/x-zip",
        "application/octet-stream",
    }
    if content_type not in allowed_content_types:
        return invalid_input_response()

    async with _conversion_semaphore:
        zip_bytes = await read_upload_with_limit(file, MAX_UPLOAD_SIZE)

    try:
        await file.close()
    except Exception:
        pass

    if not zip_bytes:
        return invalid_input_response()

    result = extract_texts_from_zip(zip_bytes)
    if result is None:
        return invalid_input_response()

    return Response(content=result, media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)