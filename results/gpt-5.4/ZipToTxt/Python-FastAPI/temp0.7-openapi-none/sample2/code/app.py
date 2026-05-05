from typing import List
import io
import zipfile

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
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


def is_text_file(filename: str) -> bool:
    lower_name = filename.lower()
    return any(lower_name.endswith(ext) for ext in TEXT_FILE_EXTENSIONS)


def extract_text_from_zip(zip_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            file_names: List[str] = sorted(
                [
                    info.filename
                    for info in zf.infolist()
                    if not info.is_dir() and is_text_file(info.filename)
                ]
            )

            if not file_names:
                raise HTTPException(status_code=400, detail="No text files found in the zip archive")

            parts: List[str] = []

            for name in file_names:
                try:
                    with zf.open(name) as file_handle:
                        raw = file_handle.read()
                except Exception:
                    raise HTTPException(status_code=400, detail=f"Failed to read file in archive: {name}")

                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    try:
                        text = raw.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Text file is not valid UTF-8: {name}",
                        )

                parts.append(text)

            return "\n".join(parts)
    except zipfile.BadZipFile:
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

    try:
        contents = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read uploaded file")
    finally:
        await file.close()

    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    result = extract_text_from_zip(contents)
    return PlainTextResponse(content=result, status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)