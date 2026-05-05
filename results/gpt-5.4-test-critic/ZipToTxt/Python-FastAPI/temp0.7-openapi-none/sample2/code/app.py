import io
import zipfile
from typing import List

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
import uvicorn

try:
    import multipart  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "python-multipart is required to handle multipart/form-data uploads."
    ) from exc


app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)


MAX_UPLOAD_SIZE = 10 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1000
MAX_TOTAL_UNCOMPRESSED_SIZE = 20 * 1024 * 1024
MAX_SINGLE_FILE_UNCOMPRESSED_SIZE = 5 * 1024 * 1024
MAX_OUTPUT_SIZE = 20 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1000
ALLOWED_CONTENT_TYPES = {
    "application/zip",
    "application/x-zip-compressed",
    "multipart/x-zip",
    "application/octet-stream",
}


def is_text_file(filename: str) -> bool:
    return filename.lower().endswith(".txt")


def _invalid_input(message: str = "Invalid input") -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def _validate_zip_info(info: zipfile.ZipInfo) -> None:
    if info.is_dir():
        return

    if info.file_size < 0 or info.compress_size < 0:
        raise _invalid_input()

    if info.file_size > MAX_SINGLE_FILE_UNCOMPRESSED_SIZE:
        raise _invalid_input()

    if info.compress_size > 0:
        ratio = info.file_size / info.compress_size
        if ratio > MAX_COMPRESSION_RATIO:
            raise _invalid_input()


def extract_text_from_zip(zip_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            infos = zf.infolist()

            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise _invalid_input()

            text_infos: List[zipfile.ZipInfo] = []
            total_uncompressed_size = 0

            for info in sorted(infos, key=lambda item: item.filename):
                if info.is_dir():
                    continue

                _validate_zip_info(info)

                if is_text_file(info.filename):
                    total_uncompressed_size += info.file_size
                    if total_uncompressed_size > MAX_TOTAL_UNCOMPRESSED_SIZE:
                        raise _invalid_input()
                    text_infos.append(info)

            if not text_infos:
                raise _invalid_input("No text files found in the zip archive")

            parts: List[str] = []
            current_output_size = 0

            for info in text_infos:
                try:
                    with zf.open(info) as file_handle:
                        raw = file_handle.read(info.file_size + 1)
                except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile, KeyError):
                    raise _invalid_input()

                if len(raw) != info.file_size:
                    raise _invalid_input()

                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    try:
                        text = raw.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        raise _invalid_input()

                current_output_size += len(text.encode("utf-8"))
                if parts:
                    current_output_size += 1

                if current_output_size > MAX_OUTPUT_SIZE:
                    raise _invalid_input()

                parts.append(text)

            return "\n".join(parts)
    except HTTPException:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, RuntimeError):
        raise _invalid_input()


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

    if file.content_type and file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Invalid input")

    contents = bytearray()
    try:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            contents.extend(chunk)
            if len(contents) > MAX_UPLOAD_SIZE:
                raise HTTPException(status_code=400, detail="Invalid input")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read uploaded file")
    finally:
        await file.close()

    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    result = extract_text_from_zip(bytes(contents))
    return PlainTextResponse(content=result, status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)