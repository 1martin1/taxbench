import os
import uuid
from fastapi import FastAPI, File, UploadFile, HTTPException, Path
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError

app = FastAPI()

# Create uploads directory if it doesn't exist
os.makedirs("uploads", exist_ok=True)

# In-memory storage for image metadata
image_metadata = {}

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    # Generate unique ID
    unique_id = str(uuid.uuid4())
    # Get original filename's extension
    original_filename = file.filename
    ext = os.path.splitext(original_filename)[1]
    # Save the file
    file_path = os.path.join("uploads", f"{unique_id}{ext}")
    try:
        with open(file_path, "wb") as buffer:
            buffer.write(await file.read())
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    # Store metadata
    image_metadata[unique_id] = {
        "content_type": file.content_type,
        "ext": ext
    }
    return {"id": unique_id}

@app.get("/images/{image_id}")
async def get_image(image_id: str):
    # Check if image_id exists in metadata
    if image_id not in image_metadata:
        raise HTTPException(status_code=404, detail="Image not found")
    metadata = image_metadata[image_id]
    file_path = os.path.join("uploads", f"{image_id}{metadata['ext']}")
    # Check if the file exists (maybe it was deleted?)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    try:
        return FileResponse(file_path, media_type=metadata["content_type"])
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=400,
        content={"error": "Invalid input"},
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)