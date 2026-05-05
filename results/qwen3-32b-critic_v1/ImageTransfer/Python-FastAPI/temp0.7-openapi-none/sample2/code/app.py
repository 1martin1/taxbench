import uuid
import os
import datetime
from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Depends
from fastapi.responses import Response as FastAPIResponse

app = FastAPI()

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
UPLOAD_RATE_LIMIT = 10  # Max uploads per window
UPLOAD_RATE_WINDOW = 60 * 60  # 1 hour in seconds

upload_counts = {}

def is_rate_limited(client_ip):
    now = datetime.datetime.utcnow()
    to_remove = []
    for ip, timestamps in upload_counts.items():
        upload_counts[ip] = [ts for ts in timestamps if (now - ts).total_seconds() < UPLOAD_RATE_WINDOW]
        if not upload_counts[ip]:
            to_remove.append(ip)
    for ip in to_remove:
        del upload_counts[ip]
    
    timestamps = upload_counts.get(client_ip, [])
    if len(timestamps) >= UPLOAD_RATE_LIMIT:
        return True
    return False

@app.post("/upload")
async def upload_image(request: Request, file: UploadFile = File(...)):
    client_ip = request.client.host
    if is_rate_limited(client_ip):
        raise HTTPException(status_code=429, detail="Too many uploads, please try again later")
    
    now = datetime.datetime.utcnow()
    if client_ip not in upload_counts:
        upload_counts[client_ip] = []
    upload_counts[client_ip].append(now)
    
    image_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, image_id)
    meta_path = os.path.join(UPLOAD_DIR, f"{image_id}.meta")
    
    try:
        total_size = 0
        with open(file_path, "wb") as buffer:
            while chunk := await file.read(1024):  # 1KB chunks
                buffer.write(chunk)
                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE:
                    os.remove(file_path)
                    raise HTTPException(status_code=400, detail="File too large")
        with open(meta_path, "w") as f:
            f.write(file.content_type)
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        if os.path.exists(meta_path):
            os.remove(meta_path)
        raise HTTPException(status_code=500, detail="Internal server error")
    
    return {"id": image_id}

@app.get("/images/{imageId}")
async def get_image(imageId: str):
    file_path = os.path.join(UPLOAD_DIR, imageId)
    meta_path = os.path.join(UPLOAD_DIR, f"{imageId}.meta")
    
    if not (os.path.exists(file_path) and os.path.exists(meta_path)):
        raise HTTPException(status_code=404, detail="Image not found")
    
    try:
        with open(meta_path, "r") as f:
            content_type = f.read().strip()
        with open(file_path, "rb") as f:
            content = f.read()
        return FastAPIResponse(
            content=content,
            media_type=content_type,
            headers={"Content-Disposition": "inline"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)