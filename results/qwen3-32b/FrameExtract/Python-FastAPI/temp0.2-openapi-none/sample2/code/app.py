import os
import subprocess
import tempfile
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Response
from typing import Annotated

app = FastAPI()

@app.post("/extract")
async def extract_frame(
    videoFileName: Annotated[str, Form(...)],
    frameNumber: Annotated[int, Form(...)],
    video: Annotated[UploadFile, File(...)]
):
    try:
        # Save uploaded video to temporary file
        with tempfile.NamedTemporaryFile(delete=False) as temp_video:
            content = await video.read()
            temp_video.write(content)
            temp_video_path = temp_video.name

        # Create temporary file for output image
        temp_image_fd, temp_image_path = tempfile.mkstemp(suffix='.png')
        os.close(temp_image_fd)

        # Build and execute ffmpeg command
        command = [
            'ffmpeg',
            '-i', temp_video_path,
            '-vf', f'select=eq(n\\,{frameNumber})',
            '-vframes', '1',
            '-y',
            temp_image_path,
            '-loglevel', 'error'
        ]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Check if output image is valid
        if os.path.exists(temp_image_path) and os.path.getsize(temp_image_path) > 0:
            with open(temp_image_path, 'rb') as f:
                image_data = f.read()
            os.unlink(temp_video_path)
            os.unlink(temp_image_path)
            return Response(content=image_data, media_type='image/png')
        else:
            os.unlink(temp_video_path)
            os.unlink(temp_image_path)
            raise HTTPException(
                status_code=404,
                detail=f"Frame at index {frameNumber} could not be found."
            )
    except Exception as e:
        # Clean up temporary files on error
        if 'temp_video_path' in locals():
            try:
                os.unlink(temp_video_path)
            except:
                pass
        if 'temp_image_path' in locals():
            try:
                os.unlink(temp_image_path)
            except:
                pass
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)