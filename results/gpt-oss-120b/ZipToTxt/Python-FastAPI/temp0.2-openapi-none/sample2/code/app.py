import io
import zipfile
from typing import List

from fastapi import FastAPI, File, HTTPException, UploadFile, Response
import uvicorn

app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)


@app.post(
    "/convert",
    summary="Convert a zip file to a text file",
    description="Convert a zip file into a single text file, concatenating the contents of all text files in the zip.",
    response_class=Response,
    responses={
        200: {
            "description": "Conversion successful",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        400: {"description": "Invalid input"},
    },
)
async def convert_zip_to_txt(file: UploadFile = File(...)):
    """
    Accept a zip file, extract all *.txt files, concatenate their contents,
    and return the combined text as `text/plain`.
    """
    # Verify that the uploaded file is a zip archive
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a .zip archive")

    # Read the uploaded file into memory (fastapi already stores it in a SpooledTemporaryFile)
    try:
        # Ensure the file pointer is at the start
        await file.seek(0)
        file_bytes = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read uploaded file")

    # Use BytesIO to work with zipfile
    zip_bytes = io.BytesIO(file_bytes)

    try:
        with zipfile.ZipFile(zip_bytes) as zip_ref:
            # Get a sorted list of text file names (case‑insensitive)
            txt_names: List[str] = sorted(
                [name for name in zip_ref.namelist() if name.lower().endswith(".txt")],
                key=lambda x: x.lower(),
            )

            if not txt_names:
                # No text files found – return empty string (still 200)
                combined_text = ""
            else:
                parts: List[str] = []
                for name in txt_names:
                    # Skip directories
                    if name.endswith("/"):
                        continue
                    with zip_ref.open(name) as f:
                        # Decode as UTF‑8, ignoring errors to be safe
                        content_bytes = f.read()
                        try:
                            content_str = content_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            content_str = content_bytes.decode("utf-8", errors="ignore")
                        parts.append(content_str)
                combined_text = "\n".join(parts)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid zip archive")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error processing zip file: {str(e)}")

    return Response(content=combined_text, media_type="text/plain")


if __name__ == "__main__":
    # Run the app on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000)