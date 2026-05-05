import os
import subprocess
import tempfile

from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import Response

app = FastAPI()

@app.post("/extract")
async def extract_frame(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...)
):
    if frameNumber < 0:
        raise HTTPException(status_code=422, detail="Frame number must be non-negative.")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "input.mp4")
        with open(video_path, "wb") as f:
            content = await video.read()
            f.write(content)
        
        output_path = os.path.join(tmpdir, "output.png")
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-vf', f'select=eq(n\\,{frameNumber})',
            '-vframes', '1',
            '-qscale', '2',
            output_path,
            '-y',
            '-loglevel', 'error'
        ]
        subprocess.run(cmd, check=False)
        
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            with open(output_path, "rb") as img_file:
                image_data = img_file.read()
            return Response(content=image_data, media_type="image/png")
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Frame at index {frameNumber} could not be found."
            )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)