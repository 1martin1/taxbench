import os
import tempfile
import zipfile
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
import uvicorn


app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)


MAX_UPLOAD_SIZE = 25 * 1024 * 1024
MAX_ENTRIES = 1000
MAX_ENTRY_UNCOMPRESSED_SIZE = 5 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_SIZE = 50 * 1024 * 1024
MAX_OUTPUT_SIZE = 50 * 1024 * 1024
READ_CHUNK_SIZE = 64 * 1024
ZIP_MAGIC = b"PK\x03\x04"


def _is_probably_zip(upload_file: UploadFile) -> bool:
    content_type = (upload_file.content_type or "").lower()
    filename = (upload_file.filename or "").lower()
    return (
        content_type in {
            "application/zip",
            "application/x-zip-compressed",
            "multipart/x-zip",
        }
        or filename.endswith(".zip")
    )


async def _save_upload_to_tempfile(upload_file: UploadFile) -> str:
    total_read = 0
    temp_file = tempfile.NamedTemporaryFile(delete=False)
    temp_path = temp_file.name

    try:
        first_chunk = await upload_file.read(READ_CHUNK_SIZE)
        if not first_chunk:
            raise HTTPException(status_code=400, detail="Invalid input: empty file")

        if len(first_chunk) < len(ZIP_MAGIC) or first_chunk[:4] != ZIP_MAGIC:
            raise HTTPException(status_code=400, detail="Invalid input")

        total_read += len(first_chunk)
        if total_read > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=400, detail="Invalid input")

        temp_file.write(first_chunk)

        while True:
            chunk = await upload_file.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            total_read += len(chunk)
            if total_read > MAX_UPLOAD_SIZE:
                raise HTTPException(status_code=400, detail="Invalid input")
            temp_file.write(chunk)

        temp_file.flush()
        return temp_path
    except Exception:
        try:
            temp_file.close()
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        raise
    finally:
        try:
            temp_file.close()
        except Exception:
            pass


def extract_texts_from_zip(zip_path: str) -> str:
    try:
        with zipfile.ZipFile(zip_path) as zip_file:
            infos = [info for info in zip_file.infolist() if not info.is_dir()]

            if len(infos) > MAX_ENTRIES:
                raise HTTPException(status_code=400, detail="Invalid input")

            infos.sort(key=lambda info: info.filename)

            total_uncompressed = 0
            output_parts = []
            output_size = 0

            for info in infos:
                if info.file_size > MAX_ENTRY_UNCOMPRESSED_SIZE:
                    raise HTTPException(status_code=400, detail="Invalid input")

                total_uncompressed += info.file_size
                if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_SIZE:
                    raise HTTPException(status_code=400, detail="Invalid input")

                decoder = None
                decoded_chunks = []
                decoded_size = 0

                try:
                    with zip_file.open(info, "r") as file_handle:
                        while True:
                            chunk = file_handle.read(READ_CHUNK_SIZE)
                            if not chunk:
                                break

                            if decoder is None:
                                if chunk.startswith(b"\xef\xbb\xbf"):
                                    decoder = "utf-8-sig"
                                else:
                                    decoder = "utf-8"

                            try:
                                text_chunk = chunk.decode(decoder)
                            except UnicodeDecodeError:
                                raise HTTPException(status_code=400, detail="Invalid input")

                            decoded_size += len(text_chunk)
                            if decoded_size > MAX_ENTRY_UNCOMPRESSED_SIZE:
                                raise HTTPException(status_code=400, detail="Invalid input")

                            output_size += len(text_chunk)
                            if output_size > MAX_OUTPUT_SIZE:
                                raise HTTPException(status_code=400, detail="Invalid input")

                            decoded_chunks.append(text_chunk)
                except HTTPException:
                    raise
                except UnicodeDecodeError:
                    raise HTTPException(status_code=400, detail="Invalid input")

                output_parts.append("".join(decoded_chunks))

            return "\n".join(output_parts)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid input")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid input")


@app.post(
    "/convert",
    response_class=PlainTextResponse,
    responses={
        200: {
            "description": "Conversion successful",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        400: {"description": "Invalid input"},
    },
    summary="Convert a zip file to a text file",
    description="Convert a zip file into a single text file, concatenating the contents of all text files in the zip.",
)
async def convert(file: Optional[UploadFile] = File(None)) -> PlainTextResponse:
    if file is None:
        raise HTTPException(status_code=400, detail="Invalid input")

    if not file.filename:
        raise HTTPException(status_code=400, detail="Invalid input")

    if not _is_probably_zip(file):
        raise HTTPException(status_code=400, detail="Invalid input")

    temp_path = None
    try:
        temp_path = await _save_upload_to_tempfile(file)
        result = extract_texts_from_zip(temp_path)
        return PlainTextResponse(content=result, status_code=200)
    finally:
        try:
            await file.close()
        except Exception:
            pass
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)