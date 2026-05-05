import os
import shutil
import subprocess
import json
from fastapi import FastAPI, File, UploadFile, HTTPException, Response
from fastapi.responses import FileResponse

app = FastAPI()

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
MAX_FRAME_NUMBER = 1_000_000  # Prevent excessive resource usage

@app.post("/extract")
async def extract_frame(
    videoFileName: str = File(...),
    frameNumber: int = File(...),
    video: UploadFile = File(...)
):
    # Check if ffmpeg is available
    if not shutil.which("ffmpeg"):
        raise HTTPException(status_code=503, detail="FFmpeg is not available")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Sanitize videoFileName to prevent path traversal
        if any(c in videoFileName for c in "\\/:*?\"<>|"):
            raise HTTPException(status_code=400, detail="Invalid characters in filename")
        video_path = os.path.join(tmpdir, videoFileName)
        video_realpath = os.path.realpath(video_path)
        tmpdir_realpath = os.path.realpath(tmpdir)
        
        if not video_realpath.startswith(tmpdir_realpath + os.path.sep):
            raise HTTPException(status_code=400, detail="Invalid path traversal in filename")
        
        # Validate frameNumber
        if frameNumber < 0:
            raise HTTPException(status_code=400, detail="Frame number must be non-negative")
        if frameNumber > MAX_FRAME_NUMBER:
            raise HTTPException(status_code=400, detail="Frame number is too large")
        
        # Write video file with size limit
        try:
            with open(video_path, "wb") as f:
                total_size = 0
                while chunk := await video.read(1024*1024):  # Read in 1MB chunks
                    total_size += len(chunk)
                    if total_size > MAX_FILE_SIZE:
                        raise HTTPException(status_code=413, detail="File size exceeds limit")
                    f.write(chunk)
        except Exception as e:
            raise HTTPException(status_code=500, detail="Error writing video file")
        
        output_path = os.path.join(tmpdir, "output.png")
        
        # Run ffmpeg command with error capture
        command = [
            "ffmpeg",
            "-i", video_path,
            "-vf", f"select=eq(n\\,{frameNumber})",
            "-vframes", "1",
            "-qscale", "5",
            "-y",
            output_path
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        if result.returncode != 0:
            error_msg = result.stderr.decode(errors="ignore")
            raise HTTPException(status_code=500, detail=f"FFmpeg error: {error_msg}")
        
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return FileResponse(output_path, media_type="image/png")
        else:
            return Response(
                content=json.dumps({"error": f"Frame at index {frameNumber} could not be found."}),
                media_type="application/json",
                status_code=404
            )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)