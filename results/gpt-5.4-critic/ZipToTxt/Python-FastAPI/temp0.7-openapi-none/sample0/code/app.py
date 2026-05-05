import io
from typing import Optional
from zipfile import BadZipFile, ZipFile

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
import uvicorn


app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)

# Resource limits to reduce risk of memory and decompression abuse.
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MiB compressed upload
MAX_ARCHIVE_ENTRIES = 1000
MAX_TOTAL_UNCOMPRESSED_TEXT_SIZE = 20 * 1024 * 1024  # 20 MiB total output
MAX_SINGLE_TEXT_FILE_SIZE = 5 * 1024 * 1024  # 5 MiB per text file
READ_CHUNK_SIZE = 64 * 1024  # 64 KiB


def _is_text_file(filename: str) -> bool:
    return filename.lower().endswith(".txt")


async def _read_upload_with_limit(upload: UploadFile, max_bytes: int) -> bytes:
    buffer = bytearray()
    while True:
        chunk = await upload.read(READ_CHUNK_SIZE)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is too large.")
    return bytes(buffer)


def _decode_text_file(raw: bytes, name: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Text file '{name}' is not valid UTF-8 text.",
            ) from exc


def _extract_texts_from_zip(zip_bytes: bytes) -> str:
    try:
        with ZipFile(io.BytesIO(zip_bytes)) as zip_file:
            infos = zip_file.infolist()

            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise HTTPException(status_code=400, detail="Zip file contains too many entries.")

            text_infos = [
                info
                for info in infos
                if not info.is_dir() and _is_text_file(info.filename)
            ]

            if not text_infos:
                return ""

            total_output_size = 0
            output_parts = []

            for info in text_infos:
                if info.file_size > MAX_SINGLE_TEXT_FILE_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Text file '{info.filename}' is too large.",
                    )

                total_output_size += info.file_size
                if total_output_size > MAX_TOTAL_UNCOMPRESSED_TEXT_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail="Total text content is too large.",
                    )

                with zip_file.open(info) as file_obj:
                    raw = file_obj.read(info.file_size + 1)
                    if len(raw) != info.file_size:
                        raise HTTPException(status_code=400, detail="Invalid zip file.")
                    if len(raw) > MAX_SINGLE_TEXT_FILE_SIZE:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Text file '{info.filename}' is too large.",
                        )

                output_parts.append(_decode_text_file(raw, info.filename))

            return "".join(output_parts)
    except HTTPException:
        raise
    except BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid zip file.") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail="Invalid zip file.") from exc


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
async def convert(file: Optional[UploadFile] = File(None)) -> PlainTextResponse:
    if file is None:
        raise HTTPException(status_code=400, detail="No file provided.")

    data = await _read_upload_with_limit(file, MAX_UPLOAD_SIZE)
    await file.close()

    if not data:
        raise HTTPException(status_code=400, detail="Uploaded zip file is empty.")

    result = _extract_texts_from_zip(data)
    return PlainTextResponse(content=result, status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)