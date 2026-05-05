import tempfile
import os
import subprocess
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse

app = FastAPI()

@app.post("/extract")
async def extract_frame(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...)
):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_video:
        content = await video.read()
        tmp_video.write(content)
        video_path = tmp_video.name

    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_img:
        image_path = tmp_img.name

    try:
        command = [
            'ffmpeg',
            '-i', video_path,
            '-vf', f'select=eq(n\\={frameNumber - 1})',
            '-vframes', '1',
            '-qscale:v', '2',
            image_path,
            '-y'
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        if result.returncode != 0 or not (os.path.exists(image_path) and os.path.getsize(image_path) > 0):
            raise HTTPException(status_code=404, detail=f"Frame at index {frameNumber} could not be found.")
            
        return FileResponse(image_path, media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Frame at index {frameNumber} could not be found.")
    finally:
        os.remove(video_path)
        if os.path.exists(image_path):
            os.remove(image_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)