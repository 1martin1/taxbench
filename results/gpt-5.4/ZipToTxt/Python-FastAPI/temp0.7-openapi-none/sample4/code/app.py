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
    ".sh",
    ".bat",
    ".sql",
    ".rst",
}


def is_text_file(filename: str) -> bool:
    lower_name = filename.lower()
    return any(lower_name.endswith(ext) for ext in TEXT_FILE_EXTENSIONS)


def decode_text_file(data: bytes) -> str:
    encodings_to_try: List[str] = ["utf-8", "utf-8-sig", "latin-1"]
    for encoding in encodings_to_try:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Unable to decode text file contents.")


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

    uploaded_bytes = await file.read()
    if not uploaded_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        zip_buffer = io.BytesIO(uploaded_bytes)
        with zipfile.ZipFile(zip_buffer, "r") as archive:
            file_infos = [info for info in archive.infolist() if not info.is_dir()]
            text_file_infos = [info for info in file_infos if is_text_file(info.filename)]

            if not text_file_infos:
                raise HTTPException(status_code=400, detail="No text files found in the zip archive.")

            concatenated_parts: List[str] = []

            for info in text_file_infos:
                try:
                    raw_content = archive.read(info)
                except Exception as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Failed to read file '{info.filename}' from zip archive.",
                    ) from exc

                try:
                    text_content = decode_text_file(raw_content)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Failed to decode text file '{info.filename}'.",
                    ) from exc

                concatenated_parts.append(f"===== {info.filename} =====\n{text_content}")

            result = "\n\n".join(concatenated_parts)
            return PlainTextResponse(content=result, status_code=200)

    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid zip file.") from exc


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)