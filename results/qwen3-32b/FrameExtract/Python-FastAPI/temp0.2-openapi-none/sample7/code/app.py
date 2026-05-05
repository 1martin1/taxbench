from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse
import tempfile
import os
import subprocess

app = FastAPI()

@app.post("/extract")
async def extract_frame(
    video_file_name: str = Form(...),
    frame_number: int = Form(...),
    video: UploadFile = File(...)
):
    if frame_number < 1:
        raise HTTPException(status_code=400, detail="Frame number must be at least 1.")
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, "input.mp4")
        with open(input_path, "wb") as f:
            content = await video.read()
            f.write(content)
        output_path = os.path.join(tmpdir, "output.png")
        frame_index = frame_number - 1
        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-vf', f'select=eq(n\\,{frame_index})',
            '-vframes', '1',
            output_path,
            '-y',
            '-loglevel', 'error'
        ]
        subprocess.run(cmd, check=False)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return FileResponse(output_path, media_type="image/png")
        else:
            raise HTTPException(status_code=404, detail=f"Frame at index {frame_number} could not be found.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)