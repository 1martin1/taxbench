from io import BytesIO
from typing import List
from zipfile import BadZipFile, ZipFile

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
import uvicorn


app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)


# Resource limits to prevent excessive memory/CPU use.
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MiB compressed upload
MAX_ARCHIVE_ENTRIES = 1000
MAX_ENTRY_UNCOMPRESSED_SIZE = 2 * 1024 * 1024  # 2 MiB per file
MAX_TOTAL_UNCOMPRESSED_SIZE = 20 * 1024 * 1024  # 20 MiB across archive
MAX_OUTPUT_SIZE = 20 * 1024 * 1024  # 20 MiB response body
MAX_COMPRESSION_RATIO = 1000


def _is_probably_text(data: bytes) -> bool:
    if not data:
        return True
    if b"\x00" in data:
        return False
    sample = data[:4096]
    text_like = 0
    for byte in sample:
        if byte in (9, 10, 13) or 32 <= byte <= 126:
            text_like += 1
    return (text_like / len(sample)) >= 0.85


def _decode_text_file(data: bytes) -> str:
    encodings_to_try = ("utf-8-sig", "utf-8", "latin-1")
    for encoding in encodings_to_try:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Unable to decode text file content")


async def _read_upload_with_limit(upload: UploadFile, max_size: int) -> bytes:
    chunks: List[bytes] = []
    total = 0
    chunk_size = 1024 * 1024

    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise HTTPException(status_code=400, detail="Uploaded file is too large")
        chunks.append(chunk)

    return b"".join(chunks)


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

    uploaded_bytes = await _read_upload_with_limit(file, MAX_UPLOAD_SIZE)
    if not uploaded_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    try:
        zip_buffer = BytesIO(uploaded_bytes)
        with ZipFile(zip_buffer) as zip_file:
            file_infos = [info for info in zip_file.infolist() if not info.is_dir()]

            if len(file_infos) > MAX_ARCHIVE_ENTRIES:
                raise HTTPException(status_code=400, detail="Zip archive contains too many files")

            total_uncompressed = 0
            for info in file_infos:
                if info.file_size < 0 or info.compress_size < 0:
                    raise HTTPException(status_code=400, detail="Invalid zip file")

                if info.file_size > MAX_ENTRY_UNCOMPRESSED_SIZE:
                    raise HTTPException(status_code=400, detail="Zip archive contains oversized files")

                total_uncompressed += info.file_size
                if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_SIZE:
                    raise HTTPException(status_code=400, detail="Zip archive is too large")

                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > MAX_COMPRESSION_RATIO:
                        raise HTTPException(status_code=400, detail="Invalid zip file")
                elif info.file_size > 0:
                    raise HTTPException(status_code=400, detail="Invalid zip file")

            # Deterministic ordering by filename.
            sorted_infos = sorted(file_infos, key=lambda item: item.filename)

            text_contents: List[str] = []
            current_output_size = 0

            for info in sorted_infos:
                with zip_file.open(info) as member_file:
                    raw_data = member_file.read(MAX_ENTRY_UNCOMPRESSED_SIZE + 1)

                if len(raw_data) > MAX_ENTRY_UNCOMPRESSED_SIZE:
                    raise HTTPException(status_code=400, detail="Zip archive contains oversized files")

                if not _is_probably_text(raw_data):
                    continue

                try:
                    decoded_content = _decode_text_file(raw_data)
                except ValueError:
                    continue

                additional_size = len(decoded_content)
                if text_contents:
                    additional_size += 1  # newline separator

                current_output_size += additional_size
                if current_output_size > MAX_OUTPUT_SIZE:
                    raise HTTPException(status_code=400, detail="Converted text is too large")

                text_contents.append(decoded_content)

            if not text_contents:
                raise HTTPException(
                    status_code=400,
                    detail="No readable text files found in the zip archive",
                )

            combined_text = "\n".join(text_contents)
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