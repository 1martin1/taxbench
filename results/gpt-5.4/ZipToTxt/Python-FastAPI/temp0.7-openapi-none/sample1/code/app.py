from io import BytesIO
import zipfile

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
import uvicorn

app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)


def _is_text_filename(filename: str) -> bool:
    return filename.lower().endswith(".txt")


def _extract_text_from_zip(zip_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            text_parts = []

            file_infos = [info for info in zf.infolist() if not info.is_dir()]

            for info in file_infos:
                if not _is_text_filename(info.filename):
                    continue

                try:
                    with zf.open(info, "r") as file_handle:
                        content = file_handle.read()
                except Exception as exc:
                    raise ValueError(f"Failed to read file '{info.filename}' from zip: {exc}") from exc

                try:
                    decoded = content.decode("utf-8")
                except UnicodeDecodeError:
                    try:
                        decoded = content.decode("utf-8-sig")
                    except UnicodeDecodeError as exc:
                        raise ValueError(
                            f"Text file '{info.filename}' is not valid UTF-8 text."
                        ) from exc

                text_parts.append(decoded)

            return "\n".join(text_parts)
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
        400: {"description": "Invalid input"},
    },
)
async def convert(file: UploadFile = File(...)) -> PlainTextResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a zip file.")

    try:
        zip_bytes = await file.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to read uploaded file: {exc}") from exc
    finally:
        await file.close()

    if not zip_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        combined_text = _extract_text_from_zip(zip_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PlainTextResponse(content=combined_text, status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)