import subprocess
import tempfile
import os
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Response

app = FastAPI()

@app.post("/extract")
async def extract_frame(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...)
):
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, videoFileName)
        with open(video_path, "wb") as buffer:
            buffer.write(await video.read())
        
        output_image_path = os.path.join(tmpdir, "output.png")
        
        command = [
            'ffmpeg',
            '-i', video_path,
            '-vf', f'select=eq(n\\{frameNumber},frame=1',
            '-vsync', '0',
            output_image_path,
            '-y'
        ]
        
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if os.path.exists(output_image_path) and os.path.getsize(output_image_path) > 0:
            with open(output_image_path, "rb") as image_file:
                return Response(content=image_file.read(), media_type="image/png")
        else:
            raise HTTPException(status_code=404, detail=f"Frame at index {frameNumber} could not be found.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)