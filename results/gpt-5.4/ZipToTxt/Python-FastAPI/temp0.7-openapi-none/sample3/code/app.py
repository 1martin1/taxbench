from typing import List
from zipfile import ZipFile, BadZipFile
import io

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
    ".sh",
    ".bat",
    ".sql",
    ".rst",
}


def is_text_file(filename: str) -> bool:
    lower_name = filename.lower()
    return any(lower_name.endswith(ext) for ext in TEXT_FILE_EXTENSIONS)


def decode_text_file(data: bytes, filename: str) -> str:
    encodings = ["utf-8", "utf-8-sig", "utf-16", "latin-1"]
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(
        status_code=400,
        detail=f"Could not decode text file: {filename}",
    )


def extract_texts_from_zip(zip_bytes: bytes) -> str:
    try:
        zip_buffer = io.BytesIO(zip_bytes)
        with ZipFile(zip_buffer, "r") as zip_file:
            file_infos = [
                info
                for info in zip_file.infolist()
                if not info.is_dir() and is_text_file(info.filename)
            ]

            if not file_infos:
                raise HTTPException(
                    status_code=400,
                    detail="No text files found in the zip archive",
                )

            parts: List[str] = []

            for info in file_infos:
                try:
                    raw_data = zip_file.read(info.filename)
                except Exception:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Failed to read file from zip archive: {info.filename}",
                    )

                text_content = decode_text_file(raw_data, info.filename)
                parts.append(text_content)

            return "\n".join(parts)
    except BadZipFile:
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

    combined_text = extract_texts_from_zip(contents)
    return PlainTextResponse(content=combined_text, status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)