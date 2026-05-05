import os
import subprocess
import tempfile
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse

app = FastAPI()

@app.post("/extract")
async def extract_frame(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...)
):
    with tempfile.NamedTemporaryFile(delete=False) as tmp_video:
        content = await video.read()
        tmp_video.write(content)
        input_path = tmp_video.name

    try:
        # Get total frames using ffprobe
        command = [
            'ffprobe',
            '-v', 'error',
            '-count_frames',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=nb_read_frames',
            '-of', 'default=nokey=1',
            input_path
        ]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            total_frames = 0
        else:
            try:
                total_frames = int(result.stdout.strip() or 0)
            except ValueError:
                total_frames = 0

        if frameNumber < 0 or frameNumber >= total_frames:
            raise HTTPException(status_code=404, detail=f"Frame at index {frameNumber} could not be found.")

        # Extract the frame using ffmpeg
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = os.path.join(tmp_dir, "output.png")
            command = [
                'ffmpeg',
                '-i', input_path,
                '-vf', f'select=eq(n,{frameNumber})',
                '-vframes', '1',
                output_path,
                '-y',
                '-loglevel', 'error'
            ]
            subprocess.run(command, check=False)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return FileResponse(output_path, media_type="image/png")
            else:
                raise HTTPException(status_code=404, detail=f"Frame at index {frameNumber} could not be found.")
    finally:
        os.remove(input_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)