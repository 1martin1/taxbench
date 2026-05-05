from typing import List
from zipfile import BadZipFile, ZipFile
from io import BytesIO

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
}


def is_text_file(filename: str) -> bool:
    lower_name = filename.lower()
    return any(lower_name.endswith(ext) for ext in TEXT_FILE_EXTENSIONS)


def decode_text_file(data: bytes) -> str:
    encodings_to_try = ("utf-8", "utf-8-sig", "latin-1")
    for encoding in encodings_to_try:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Unable to decode text file content")


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

    uploaded_bytes = await file.read()
    if not uploaded_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    try:
        zip_buffer = BytesIO(uploaded_bytes)
        with ZipFile(zip_buffer) as zip_file:
            file_infos = [info for info in zip_file.infolist() if not info.is_dir()]
            text_contents: List[str] = []

            for info in file_infos:
                if not is_text_file(info.filename):
                    continue

                with zip_file.open(info) as member_file:
                    raw_data = member_file.read()

                try:
                    decoded_content = decode_text_file(raw_data)
                except ValueError:
                    continue

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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)