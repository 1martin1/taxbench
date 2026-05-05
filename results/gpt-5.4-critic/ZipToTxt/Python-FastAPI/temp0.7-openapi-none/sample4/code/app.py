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

# Resource limits to prevent excessive memory/CPU consumption.
MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 MiB compressed upload
MAX_ZIP_ENTRIES = 1000
MAX_SINGLE_TEXT_FILE_SIZE = 5 * 1024 * 1024  # 5 MiB uncompressed per text file
MAX_TOTAL_UNCOMPRESSED_TEXT_SIZE = 20 * 1024 * 1024  # 20 MiB total extracted text


def _is_text_file(filename: str) -> bool:
    return filename.lower().endswith(".txt")


async def _read_upload_with_limit(upload: UploadFile, max_bytes: int) -> bytes:
    chunks: List[bytes] = []
    total = 0

    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is too large.")
        chunks.append(chunk)

    return b"".join(chunks)


def _read_zip_entry_with_limit(file_obj, max_bytes: int) -> bytes:
    chunks: List[bytes] = []
    total = 0

    while True:
        chunk = file_obj.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=400, detail="A text file in the ZIP is too large.")
        chunks.append(chunk)

    return b"".join(chunks)


def _extract_texts_from_zip(zip_bytes: bytes) -> str:
    try:
        with ZipFile(io.BytesIO(zip_bytes)) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise HTTPException(status_code=400, detail="ZIP file contains too many entries.")

            text_infos = [
                info
                for info in infos
                if not info.is_dir() and _is_text_file(info.filename)
            ]

            if not text_infos:
                return ""

            text_infos.sort(key=lambda info: info.filename)

            contents: List[str] = []
            total_text_size = 0

            for info in text_infos:
                if info.file_size > MAX_SINGLE_TEXT_FILE_SIZE:
                    raise HTTPException(status_code=400, detail="A text file in the ZIP is too large.")

                total_text_size += info.file_size
                if total_text_size > MAX_TOTAL_UNCOMPRESSED_TEXT_SIZE:
                    raise HTTPException(status_code=400, detail="Total extracted text is too large.")

                with zf.open(info) as file_obj:
                    raw = _read_zip_entry_with_limit(file_obj, MAX_SINGLE_TEXT_FILE_SIZE)

                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Text file '{info.filename}' is not valid UTF-8 text.",
                    )

                contents.append(text)

            combined_text = "\n".join(contents)
            if len(combined_text.encode("utf-8")) > MAX_TOTAL_UNCOMPRESSED_TEXT_SIZE + max(0, len(contents) - 1):
                raise HTTPException(status_code=400, detail="Total extracted text is too large.")

            return combined_text
    except HTTPException:
        raise
    except (BadZipFile, LargeZipFile, OSError, ValueError, RuntimeError):
        raise HTTPException(status_code=400, detail="Invalid ZIP file.")


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
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {
                                "type": "string",
                                "format": "binary",
                            }
                        },
                    }
                }
            },
        }
    },
)
async def convert(file: UploadFile = File(...)) -> PlainTextResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    data = await _read_upload_with_limit(file, MAX_UPLOAD_SIZE)
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    combined_text = _extract_texts_from_zip(data)
    return PlainTextResponse(content=combined_text, status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)