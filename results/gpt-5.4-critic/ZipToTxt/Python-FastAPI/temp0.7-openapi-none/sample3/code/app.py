import io
from typing import List
from zipfile import BadZipFile, ZipFile

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse
import uvicorn


app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)


MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MiB
MAX_ARCHIVE_ENTRIES = 1000
MAX_UNCOMPRESSED_ENTRY_SIZE = 2 * 1024 * 1024  # 2 MiB
MAX_TOTAL_UNCOMPRESSED_SIZE = 20 * 1024 * 1024  # 20 MiB
MAX_OUTPUT_SIZE = 20 * 1024 * 1024  # 20 MiB
MAX_COMPRESSION_RATIO = 100


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
}


def is_text_file(filename: str) -> bool:
    lower_name = filename.lower()
    return any(lower_name.endswith(ext) for ext in TEXT_FILE_EXTENSIONS)


def decode_text(data: bytes) -> str:
    encodings = ("utf-8", "utf-8-sig", "utf-16", "latin-1")
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Unable to decode file as text")


async def read_upload_with_limit(upload_file: UploadFile, max_bytes: int) -> bytes:
    chunks: List[bytes] = []
    total = 0

    while True:
        chunk = await upload_file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is too large")
        chunks.append(chunk)

    return b"".join(chunks)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> PlainTextResponse:
    return PlainTextResponse(content="Invalid input", status_code=400)


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
async def convert(file: UploadFile = File(None)) -> PlainTextResponse:
    if file is None or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    content_type = (file.content_type or "").lower()
    allowed_content_types = {
        "application/zip",
        "application/x-zip-compressed",
        "multipart/x-zip",
        "application/octet-stream",
    }
    if content_type and content_type not in allowed_content_types:
        raise HTTPException(status_code=400, detail="Invalid input")

    try:
        uploaded_bytes = await read_upload_with_limit(file, MAX_UPLOAD_SIZE)
        if not uploaded_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        try:
            zip_buffer = io.BytesIO(uploaded_bytes)
            with ZipFile(zip_buffer) as zip_file:
                file_infos = zip_file.infolist()
                if len(file_infos) > MAX_ARCHIVE_ENTRIES:
                    raise HTTPException(status_code=400, detail="Invalid input")

                text_file_infos = []
                total_uncompressed_size = 0

                for info in file_infos:
                    if info.is_dir():
                        continue

                    if info.file_size < 0 or info.compress_size < 0:
                        raise HTTPException(status_code=400, detail="Invalid input")

                    if info.file_size > MAX_UNCOMPRESSED_ENTRY_SIZE:
                        raise HTTPException(status_code=400, detail="Invalid input")

                    total_uncompressed_size += info.file_size
                    if total_uncompressed_size > MAX_TOTAL_UNCOMPRESSED_SIZE:
                        raise HTTPException(status_code=400, detail="Invalid input")

                    if info.compress_size > 0:
                        ratio = info.file_size / info.compress_size
                        if ratio > MAX_COMPRESSION_RATIO:
                            raise HTTPException(status_code=400, detail="Invalid input")
                    elif info.file_size > 0:
                        raise HTTPException(status_code=400, detail="Invalid input")

                    if is_text_file(info.filename):
                        text_file_infos.append(info)

                if not text_file_infos:
                    raise HTTPException(
                        status_code=400,
                        detail="No text files found in the zip archive",
                    )

                text_file_infos.sort(key=lambda info: info.filename)

                combined_parts: List[str] = []
                current_output_size = 0

                for info in text_file_infos:
                    try:
                        with zip_file.open(info, "r") as entry_file:
                            content_bytes = entry_file.read(MAX_UNCOMPRESSED_ENTRY_SIZE + 1)
                    except Exception:
                        raise HTTPException(
                            status_code=400,
                            detail="Failed to read file from zip archive",
                        )

                    if len(content_bytes) > MAX_UNCOMPRESSED_ENTRY_SIZE:
                        raise HTTPException(status_code=400, detail="Invalid input")

                    try:
                        content_text = decode_text(content_bytes)
                    except ValueError:
                        continue

                    part = f"===== {info.filename} =====\n{content_text}"
                    current_output_size += len(part.encode("utf-8"))
                    if combined_parts:
                        current_output_size += len("\n\n".encode("utf-8"))

                    if current_output_size > MAX_OUTPUT_SIZE:
                        raise HTTPException(status_code=400, detail="Invalid input")

                    combined_parts.append(part)

                if not combined_parts:
                    raise HTTPException(
                        status_code=400,
                        detail="No decodable text files found in the zip archive",
                    )

                combined_text = "\n\n".join(combined_parts)
                return PlainTextResponse(content=combined_text, status_code=200)

        except BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid zip file")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        await file.close()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)