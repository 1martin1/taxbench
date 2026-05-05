from fastapi import FastAPI, File, UploadFile, Form, HTTPException
import subprocess
import tempfile
import os
import asyncio

app = FastAPI()

# Security: Limit maximum file size (adjust as needed)
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
# Concurrency control: Limit simultaneous ffmpeg processes
CONCURRENCY_LIMIT = 5
semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

@app.post("/extract")
async def extract_frame(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...)
):
    if frameNumber < 0:
        raise HTTPException(status_code=400, detail="Frame number must be non-negative")
    
    if video.size > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File size exceeds maximum allowed size of 100MB")
    
    async with semaphore:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, "input.mp4")
            content = await video.read()
            if len(content) > MAX_FILE_SIZE:
                raise HTTPException(status_code=413, detail="File size exceeds maximum allowed size of 100MB")
            with open(input_path, "wb") as buffer:
                buffer.write(content)
            
            output_path = os.path.join(tmpdir, "output.png")
            
            command = [
                'ffmpeg',
                '-i', input_path,
                '-vf', f"select=eq(n\\,{frameNumber})",
                '-vframes', '1',
                '-y',
                output_path
            ]
            
            try:
                result = subprocess.run(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True
                )
            except (subprocess.CalledProcessError, FileNotFoundError, OSError):
                raise HTTPException(
                    status_code=500,
                    detail="FFmpeg processing failed - check server configuration"
                )
            
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                with open(output_path, "rb") as f:
                    image_data = f.read()
                return Response(content=image_data, media_type="image/png")
            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"Frame at second {frameNumber} could not be found."
                )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)