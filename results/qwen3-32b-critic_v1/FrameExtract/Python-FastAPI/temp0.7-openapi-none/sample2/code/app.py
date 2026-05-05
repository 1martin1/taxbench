from fastapi import FastAPI, File, Form, UploadFile, BackgroundTasks, HTTPException, Response
import tempfile
import os
import subprocess
import re

app = FastAPI()

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB

@app.post("/extract")
async def extract_frame(
    video_file_name: str = Form(...), 
    frame_number: int = Form(...), 
    video: UploadFile = File(...),
    background_tasks: BackgroundTasks = None
):
    # Validate frame number is non-negative
    if frame_number < 0:
        raise HTTPException(status_code=422, detail="Frame number must be non-negative")
    
    # Sanitize video file name
    video_file_name = re.sub(r"[^\w\.-]", "_", video_file_name)
    
    input_temp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    input_path = input_temp.name
    try:
        content = await video.read()
        if len(content) > MAX_FILE_SIZE:
            input_temp.close()
            os.unlink(input_path)
            raise HTTPException(status_code=413, detail="File too large")
        input_temp.write(content)
        input_temp.close()
    except Exception as e:
        input_temp.close()
        os.unlink(input_path)
        raise HTTPException(status_code=500, detail="Error processing video file") from e

    output_path = input_path + "_output.png"
    command = [
        'ffmpeg',
        '-i', input_path,
        '-vf', f'select=eq(n\\,{frame_number})',
        '-vframes', '1',
        '-qscale', '5',
        output_path,
        '-y',
        '-loglevel', 'error'
    ]
    
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        os.unlink(input_path)
        if os.path.exists(output_path):
            os.unlink(output_path)
        raise HTTPException(status_code=500, detail="Error processing video with ffmpeg") from e

    try:
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            try:
                with open(output_path, "rb") as f:
                    image_data = f.read()
            except Exception as e:
                os.unlink(input_path)
                os.unlink(output_path)
                raise HTTPException(status_code=500, detail="Error reading output image") from e

            if background_tasks is not None:
                background_tasks.add_task(os.unlink, input_path)
                background_tasks.add_task(os.unlink, output_path)

            return Response(content=image_data, media_type="image/png")
        else:
            os.unlink(input_path)
            if os.path.exists(output_path):
                os.unlink(output_path)
            raise HTTPException(
                status_code=404, 
                detail=f"Frame at second {frame_number} could not be found."
            )
    except Exception as e:
        # Fallback cleanup for unexpected errors
        if os.path.exists(input_path):
            os.unlink(input_path)
        if os.path.exists(output_path):
            os.unlink(output_path)
        raise

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)