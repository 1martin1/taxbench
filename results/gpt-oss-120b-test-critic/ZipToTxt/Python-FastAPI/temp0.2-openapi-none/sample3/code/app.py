import io
import logging
import zipfile

from fastapi import FastAPI, File, HTTPException, UploadFile, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# ----------------------------------------------------------------------
# Middleware to enforce upload size limits
# ----------------------------------------------------------------------
class LimitUploadSizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_UPLOAD_SIZE:
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "Uploaded file is too large"},
                    )
            except ValueError:
                # If the header is malformed we let the request proceed;
                # the endpoint will handle any issues.
                pass
        return await call_next(request)


# ----------------------------------------------------------------------
# FastAPI application
# ----------------------------------------------------------------------
app = FastAPI(
    title="ZipToTxt",
    description="A simple webapp API for converting zip files into a single text file.",
    version="1.0.11",
)

app.add_middleware(LimitUploadSizeMiddleware)


# ----------------------------------------------------------------------
# Endpoint
# ----------------------------------------------------------------------
@app.post(
    "/convert",
    response_class=Response,
    responses={
        200: {"description": "Conversion successful", "content": {"text/plain": {}}},
        400: {"description": "Invalid input"},
    },
)
async def convert(file: UploadFile = File(None)):
    """
    Convert a zip file into a single text file, concatenating the contents of all
    text files in the zip.
    """
    # Validate presence of the file (return 400 instead of FastAPI's default 422)
    if file is None:
        raise HTTPException(status_code=400, detail="File is required")

    try:
        # Ensure the file pointer is at the start
        file.file.seek(0)

        # Attempt to open the uploaded file as a zip archive
        with zipfile.ZipFile(file.file) as z:
            text_parts = []
            for info in z.infolist():
                # Skip directories
                if info.is_dir():
                    continue

                # Read each entry
                with z.open(info) as inner_file:
                    data = inner_file.read()
                    try:
                        text = data.decode("utf-8")
                    except UnicodeDecodeError:
                        # Not a UTF‑8 text file; skip it
                        continue
                    text_parts.append(text)

            combined_text = "\n".join(text_parts)

    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")
    except Exception:
        # Log the unexpected error internally but do not expose details to the client
        logging.exception("Unexpected error while processing the uploaded zip file")
        raise HTTPException(
            status_code=400, detail="Failed to process the uploaded file"
        )

    return Response(content=combined_text, media_type="text/plain")


# ----------------------------------------------------------------------
# Run the application
# ----------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)