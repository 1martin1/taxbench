from fastapi import FastAPI, File, UploadFile, Form, HTTPException
import subprocess
import tempfile
import os
from starlette.responses import Response
import shutil

app = FastAPI()

@app.post("/extract")
async def extract_frame(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...)
):
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_video_path = os.path.join(tmpdir, "input.mp4")
        with open(temp_video_path, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)
        
        temp_output_image_path = os.path.join(tmpdir, "output.png")
        
        command = [
            'ffmpeg',
            '-i', temp_video_path,
            '-vf', f'select=n={frameNumber}',
            '-vframes', '1',
            '-vsync', '0',
            temp_output_image_path,
            '-y'
        ]
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if os.path.exists(temp_output_image_path) and os.path.getsize(temp_output_image_path) > 0:
            with open(temp_output_image_path, "rb") as image_file:
                image_data = image_file.read()
            return Response(content=image_data, media_type="image/png")
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Frame at index {frameNumber} could not be found."
            )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)