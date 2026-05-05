import io
import zipfile
from fastapi import FastAPI, UploadFile, File, HTTPException, Response
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
    responses={
        200: {
            "description": "Conversion successful",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        400: {"description": "Invalid input"},
    },
)
async def convert(file: UploadFile = File(...)):
    """
    Accept a zip file, extract all *.txt files, concatenate their contents,
    and return the combined text as plain text.
    """
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Uploaded file is not a zip archive.")

    # Read the uploaded file into memory
    try:
        file_bytes = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read uploaded file: {e}")

    # Open the zip archive
    try:
        zip_bytes = io.BytesIO(file_bytes)
        with zipfile.ZipFile(zip_bytes) as zip_ref:
            # Gather text from all .txt files (case‑insensitive)
            combined_text_parts = []
            for zip_info in zip_ref.infolist():
                # Skip directories
                if zip_info.is_dir():
                    continue
                # Process only files ending with .txt (ignore case)
                if not zip_info.filename.lower().endswith(".txt"):
                    continue
                with zip_ref.open(zip_info) as f:
                    try:
                        # Decode as UTF‑8, replace undecodable bytes
                        file_content = f.read().decode("utf-8", errors="replace")
                    except Exception:
                        # If decoding fails, skip this file
                        continue
                    combined_text_parts.append(file_content)
            combined_text = "\n".join(combined_text_parts)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error processing zip file: {e}")

    return Response(content=combined_text, media_type="text/plain")


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000)