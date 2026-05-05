from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse
import tempfile
import os
import subprocess

app = FastAPI()

MAX_FRAME = 10**6  # Prevent excessive frame index values (CWE-400)
MAX_UPLOAD_SIZE = 1024 * 1024 * 1024  # 1 GB (adjust based on your requirements)

@app.post("/extract")
async def extract_frame(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...)
):
    # Validate frame number range
    if frameNumber < 0:
        raise HTTPException(status_code=400, detail="Frame number must be non-negative")
    if frameNumber > MAX_FRAME:
        raise HTTPException(status_code=400, detail=f"Frame number must be ≤ {MAX_FRAME}")
    
    # Sanitize filename and create safe path
    safe_filename = os.path.basename(videoFileName)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, safe_filename)
        
        # Stream video file to disk in chunks (prevents memory exhaustion)
        with open(video_path, "wb") as f:
            bytes_written = 0
            while chunk := await video.read(1024 * 1024):  # 1MB chunks
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_SIZE:
                    raise HTTPException(status_code=413, detail="File too large")
                f.write(chunk)
        
        output_path = os.path.join(tmpdir, "output.png")
        
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-vf', f"select=eq(n\\,{frameNumber})",
            '-vframes', '1',
            output_path,
            '-y',
            '-loglevel', 'quiet'
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return FileResponse(output_path, media_type="image/png")
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Frame at index {frameNumber} could not be found."
            )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)