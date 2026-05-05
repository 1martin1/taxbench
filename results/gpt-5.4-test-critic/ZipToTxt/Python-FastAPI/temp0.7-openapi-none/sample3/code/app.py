import io
from typing import List
from zipfile import BadZipFile, LargeZipFile, ZipFile

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
import uvicorn


app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)


MAX_UPLOAD_SIZE = 10 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1000
MAX_TOTAL_UNCOMPRESSED_SIZE = 50 * 1024 * 1024
MAX_OUTPUT_SIZE = 50 * 1024 * 1024
MAX_SINGLE_FILE_SIZE = 5 * 1024 * 1024
ALLOWED_UPLOAD_CONTENT_TYPES = {
    "application/zip",
    "application/x-zip-compressed",
    "multipart/x-zip",
}


def is_text_file(filename: str) -> bool:
    return filename.lower().endswith(".txt")


def decode_text_file(data: bytes, filename: str) -> str:
    encodings = ("utf-8", "utf-8-sig", "utf-16")
    for encoding in encodings:
        try:
            decoded = data.decode(encoding)
            if "\x00" in decoded:
                raise HTTPException(
                    status_code=400,
                    detail=f"File is not valid text: {filename}",
                )
            return decoded
        except UnicodeDecodeError:
            continue

    raise HTTPException(
        status_code=400,
        detail=f"Could not decode text file: {filename}",
    )


def read_upload_with_limit(upload: UploadFile, max_size: int) -> bytes:
    chunks: List[bytes] = []
    total_size = 0

    while True:
        chunk = upload.file.read(1024 * 1024)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > max_size:
            raise HTTPException(status_code=400, detail="Uploaded file is too large")
        chunks.append(chunk)

    return b"".join(chunks)


def extract_texts_from_zip(zip_bytes: bytes) -> str:
    try:
        with ZipFile(io.BytesIO(zip_bytes), "r") as zip_file:
            file_infos = zip_file.infolist()

            if len(file_infos) > MAX_ARCHIVE_ENTRIES:
                raise HTTPException(status_code=400, detail="Zip archive contains too many entries")

            text_file_infos = []
            total_uncompressed_size = 0

            for info in file_infos:
                if info.is_dir():
                    continue

                total_uncompressed_size += info.file_size
                if total_uncompressed_size > MAX_TOTAL_UNCOMPRESSED_SIZE:
                    raise HTTPException(status_code=400, detail="Zip archive is too large when extracted")

                if is_text_file(info.filename):
                    if info.file_size > MAX_SINGLE_FILE_SIZE:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Text file is too large: {info.filename}",
                        )
                    text_file_infos.append(info)

            if not text_file_infos:
                raise HTTPException(
                    status_code=400,
                    detail="No text files found in the zip archive",
                )

            parts: List[str] = []
            current_output_size = 0

            for info in text_file_infos:
                try:
                    with zip_file.open(info, "r") as entry_file:
                        raw_data = entry_file.read(MAX_SINGLE_FILE_SIZE + 1)
                except (KeyError, OSError, ValueError, RuntimeError, BadZipFile, LargeZipFile):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Failed to read file from zip archive: {info.filename}",
                    )

                if len(raw_data) != info.file_size or len(raw_data) > MAX_SINGLE_FILE_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Text file is too large or invalid: {info.filename}",
                    )

                text_content = decode_text_file(raw_data, info.filename)
                current_output_size += len(text_content.encode("utf-8"))
                if parts:
                    current_output_size += 1

                if current_output_size > MAX_OUTPUT_SIZE:
                    raise HTTPException(status_code=400, detail="Converted text is too large")

                parts.append(text_content)

            return "\n".join(parts)
    except HTTPException:
        raise
    except (BadZipFile, LargeZipFile):
        raise HTTPException(status_code=400, detail="Invalid zip file")


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
        raise HTTPException(status_code=400, detail="No file provided")

    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a zip file")

    if file.content_type and file.content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Uploaded file must be a zip file")

    try:
        contents = read_upload_with_limit(file, MAX_UPLOAD_SIZE)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read uploaded file")
    finally:
        await file.close()

    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    combined_text = extract_texts_from_zip(contents)
    return PlainTextResponse(content=combined_text, status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)