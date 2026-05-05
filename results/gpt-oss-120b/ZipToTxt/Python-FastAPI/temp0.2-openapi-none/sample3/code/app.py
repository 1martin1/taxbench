import io
import zipfile

from fastapi import FastAPI, File, HTTPException, UploadFile, Response
import uvicorn

app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)


@app.post(
    "/convert",
    response_class=Response,
    responses={
        200: {"description": "Conversion successful", "content": {"text/plain": {}}},
        400: {"description": "Invalid input"},
    },
)
async def convert(file: UploadFile = File(...)):
    """
    Convert a zip file into a single text file, concatenating the contents of all
    text files in the zip.
    """
    # Basic validation – ensure we received a zip archive
    if not (file.content_type == "application/zip" or file.filename.lower().endswith(".zip")):
        raise HTTPException(status_code=400, detail="Uploaded file must be a zip archive")

    try:
        # Read the whole uploaded file into memory
        raw_bytes = await file.read()
        zip_bytes = io.BytesIO(raw_bytes)

        with zipfile.ZipFile(zip_bytes) as z:
            text_parts = []
            for info in z.infolist():
                # Skip directories
                if info.is_dir():
                    continue

                # Open each file inside the zip
                with z.open(info) as inner_file:
                    data = inner_file.read()
                    try:
                        # Try to decode as UTF‑8; if it fails we treat it as non‑text
                        text = data.decode("utf-8")
                    except UnicodeDecodeError:
                        continue  # Not a text file, skip it
                    text_parts.append(text)

            # Join all collected texts with a newline separator
            combined_text = "\n".join(text_parts)

    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")
    except Exception as e:
        # Catch‑all for unexpected errors
        raise HTTPException(status_code=400, detail=str(e))

    return Response(content=combined_text, media_type="text/plain")


if __name__ == "__main__":
    # Run the FastAPI app on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000)