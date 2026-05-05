from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Response
import subprocess
import tempfile
import os

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
        tmp_video_path = tmp_video.name

    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_img:
        tmp_img_path = tmp_img.name

    try:
        command = [
            'ffmpeg',
            '-i', tmp_video_path,
            '-vf', f'select=eq(n\\,{frameNumber})',
            '-vframes', '1',
            tmp_img_path,
            '-y',
            '-loglevel', 'error'
        ]
        try:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            raise HTTPException(status_code=404, detail=f"Frame at index {frameNumber} could not be found.")
        
        if os.path.exists(tmp_img_path) and os.path.getsize(tmp_img_path) > 0:
            with open(tmp_img_path, "rb") as img_file:
                img_data = img_file.read()
            return Response(content=img_data, media_type="image/png")
        else:
            raise HTTPException(status_code=404, detail=f"Frame at index {frameNumber} could not be found.")
    finally:
        os.remove(tmp_video_path)
        if os.path.exists(tmp_img_path):
            os.remove(tmp_img_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)