import os
import subprocess
import tempfile
from fastapi import FastAPI, File, Form, UploadFile, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

app = FastAPI()

@app.post("/extract")
async def extract_frame(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    # Validate frame number is non-negative
    if frameNumber < 0:
        raise HTTPException(
            status_code=422,
            detail="Frame number must be non-negative."
        )

    # Create temporary video file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_video:
        content = await video.read()
        tmp_video.write(content)
    tmp_video_path = tmp_video.name

    # Create temporary output directory
    output_dir = tempfile.mkdtemp()
    output_path = os.path.join(output_dir, "frame.png")

    # Construct ffmpeg command
    command = [
        'ffmpeg',
        '-i', tmp_video_path,
        '-vf', f"select=n={frameNumber}",
        '-vframes', '1',
        '-vsync', '0',
        output_path,
        '-y'
    ]

    try:
        # Execute ffmpeg command
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except FileNotFoundError:
        # Handle missing ffmpeg
        os.remove(tmp_video_path)
        os.rmdir(output_dir)
        raise HTTPException(
            status_code=500,
            detail="FFmpeg is not installed."
        )

    # Check ffmpeg execution result
    if result.returncode != 0:
        os.remove(tmp_video_path)
        os.rmdir(output_dir)
        raise HTTPException(
            status_code=404,
            detail=f"Frame at second {frameNumber} could not be found."
        )

    # Verify output file was created
    if not (os.path.exists(output_path) and os.path.getsize(output_path) > 0):
        os.remove(tmp_video_path)
        os.rmdir(output_dir)
        raise HTTPException(
            status_code=404,
            detail=f"Frame at second {frameNumber} could not be found."
        )

    # Schedule cleanup tasks
    background_tasks.add_task(os.remove, tmp_video_path)
    background_tasks.add_task(os.remove, output_path)
    background_tasks.add_task(os.rmdir, output_dir)

    return FileResponse(output_path, media_type="image/png")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)