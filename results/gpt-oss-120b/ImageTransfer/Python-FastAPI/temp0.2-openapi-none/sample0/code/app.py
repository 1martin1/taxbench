import os
import uuid
import mimetypes
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, status, Response
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(
    title="Image Upload and Share API",
    description="An API for uploading images and generating shareable links.",
    version="1.0.0",
)

# Directory to store uploaded images
BASE_DIR = Path(__file__).parent
IMAGE_DIR = BASE_DIR / "images"
IMAGE_DIR.mkdir(exist_ok=True)


def _save_upload_file(upload_file: UploadFile) -> str:
    """
    Save the uploaded file to disk and return the generated image ID.
    """
    # Generate a unique identifier
    image_id = uuid.uuid4().hex

    # Preserve original extension if any
    suffix = Path(upload_file.filename).suffix
    filename = f"{image_id}{suffix}"
    file_path = IMAGE_DIR / filename

    try:
        # Read the entire file into memory (acceptable for typical image sizes)
        contents = upload_file.file.read()
        with open(file_path, "wb") as f:
            f.write(contents)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save uploaded file.",
        ) from e
    finally:
        upload_file.file.close()

    return image_id


def _find_image_path(image_id: str) -> Path:
    """
    Locate the image file on disk based on its ID.
    Returns the Path if found, otherwise raises HTTPException 404.
    """
    matches = list(IMAGE_DIR.glob(f"{image_id}*"))
    if not matches:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found.",
        )
    # Return the first match (there should be only one)
    return matches[0]


@app.post("/upload", summary="Upload an image. Accept all formats.", response_model=dict)
async def upload_image(file: UploadFile = File(...)):
    """
    Allow users to upload images, returning a shareable link (image ID).
    """
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided.",
        )
    image_id = _save_upload_file(file)
    return {"id": image_id}


@app.get(
    "/images/{image_id}",
    summary="Share an image",
    responses={
        200: {
            "content": {"*/*": {}},
            "description": "The image file being viewed. Mimetype of original image is preserved.",
        },
        404: {"description": "Image not found"},
        500: {"description": "Internal server error"},
    },
)
def get_image(image_id: str):
    """
    Retrieve an image for viewing in the browser.
    """
    try:
        image_path = _find_image_path(image_id)
        # Guess MIME type based on file extension
        mime_type, _ = mimetypes.guess_type(str(image_path))
        if mime_type is None:
            mime_type = "application/octet-stream"

        with open(image_path, "rb") as f:
            data = f.read()

        # Set Content-Disposition to inline so browsers display it rather than download
        headers = {"Content-Disposition": "inline"}
        return Response(content=data, media_type=mime_type, headers=headers)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve image.",
        ) from e


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)