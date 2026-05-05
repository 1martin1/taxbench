from io import BytesIO
import zipfile

from fastapi import FastAPI, File, UploadFile
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
MAX_OUTPUT_SIZE = 20 * 1024 * 1024  # 20 MiB
READ_CHUNK_SIZE = 64 * 1024


def _bad_request(message: str) -> PlainTextResponse:
    return PlainTextResponse(content=message, status_code=400)


def _is_text_filename(filename: str) -> bool:
    return filename.lower().endswith(".txt")


def _read_upload_with_limit(upload_file: UploadFile, max_size: int) -> bytes:
    chunks = []
    total_size = 0

    while True:
        chunk = upload_file.file.read(READ_CHUNK_SIZE)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > max_size:
            raise ValueError("Uploaded file is too large.")
        chunks.append(chunk)

    if total_size == 0:
        raise ValueError("Uploaded file is empty.")

    return b"".join(chunks)


def _extract_text_from_zip(zip_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            file_infos = [info for info in zf.infolist() if not info.is_dir()]

            if len(file_infos) > MAX_ENTRIES:
                raise ValueError("Zip file contains too many entries.")

            total_uncompressed = 0
            output_parts = []
            output_size = 0
            text_file_count = 0

            for info in file_infos:
                if not _is_text_filename(info.filename):
                    continue

                text_file_count += 1

                if info.file_size > MAX_ENTRY_UNCOMPRESSED_SIZE:
                    raise ValueError("A text file in the zip is too large.")

                total_uncompressed += info.file_size
                if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_SIZE:
                    raise ValueError("Zip file contents are too large.")

                try:
                    with zf.open(info, "r") as file_handle:
                        content_chunks = []
                        bytes_read = 0

                        while True:
                            chunk = file_handle.read(READ_CHUNK_SIZE)
                            if not chunk:
                                break
                            bytes_read += len(chunk)
                            if bytes_read > MAX_ENTRY_UNCOMPRESSED_SIZE:
                                raise ValueError("A text file in the zip is too large.")
                            content_chunks.append(chunk)

                        content = b"".join(content_chunks)
                except ValueError:
                    raise
                except Exception as exc:
                    raise ValueError("Failed to read zip contents.") from exc

                try:
                    decoded = content.decode("utf-8-sig")
                except UnicodeDecodeError as exc:
                    raise ValueError("A text file in the zip is not valid UTF-8 text.") from exc

                additional_size = len(decoded)
                if output_parts:
                    additional_size += 1

                output_size += additional_size
                if output_size > MAX_OUTPUT_SIZE:
                    raise ValueError("Converted text is too large.")

                output_parts.append(decoded)

            if text_file_count == 0:
                return ""

            return "\n".join(output_parts)
    except zipfile.BadZipFile as exc:
        raise ValueError("Uploaded file is not a valid zip file.") from exc


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
        400: {
            "description": "Invalid input",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
    },
)
async def convert(file: UploadFile = File(...)) -> PlainTextResponse:
    if file is None:
        return _bad_request("No file provided.")

    try:
        zip_bytes = _read_upload_with_limit(file, MAX_UPLOAD_SIZE)
    except ValueError as exc:
        return _bad_request(str(exc))
    except Exception:
        return _bad_request("Failed to read uploaded file.")
    finally:
        await file.close()

    try:
        combined_text = _extract_text_from_zip(zip_bytes)
    except ValueError as exc:
        return _bad_request(str(exc))

    return PlainTextResponse(content=combined_text, status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)