from fastapi import FastAPI, File, UploadFile, Form, Response, HTTPException
import subprocess
import os
import tempfile

app = FastAPI()

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_FRAME_NUMBER = 1000000  # Maximum allowed frame index

@app.post("/extract")
async def extract_frame(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...)
):
    # Validate frameNumber bounds
    if frameNumber < 0:
        raise HTTPException(status_code=400, detail="Frame number must be non-negative")
    if frameNumber > MAX_FRAME_NUMBER:
        raise HTTPException(status_code=400, detail="Frame number is too large")

    # Validate file size
    if video.size > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File size exceeds the maximum allowed size")

    # Create temporary video file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_video:
        content = await video.read()
        temp_video.write(content)
        temp_video_path = temp_video.name

    # Create temporary image file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_image:
        output_image_path = temp_image.name

    try:
        # Construct correct ffmpeg command with proper quoting and escaping
        command = [
            'ffmpeg',
            '-i', temp_video_path,
            '-vf', f"select='eq(n\\,{frameNumber})'",
            '-vframes', '1',
            output_image_path,
            '-y'
        ]
        
        # Execute ffmpeg command
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)

        # Check if output image exists and is non-empty
        if not os.path.exists(output_image_path) or os.path.getsize(output_image_path) == 0:
            raise HTTPException(status_code=404, detail=f"Frame at index {frameNumber} could not be found.")

        # Return the extracted frame
        with open(output_image_path, "rb") as image_file:
            image_data = image_file.read()
        return Response(content=image_data, media_type="image/png")

    except FileNotFoundError as e:
        # Handle missing ffmpeg binary
        raise HTTPException(status_code=500, detail="FFmpeg is not installed or not found in PATH")
    except Exception as e:
        # Handle all other uncaught exceptions with 500
        raise HTTPException(status_code=500, detail="Internal server error occurred")
    finally:
        # Clean up temporary files
        if os.path.exists(temp_video_path):
            os.remove(temp_video_path)
        if os.path.exists(output_image_path):
            os.remove(output_image_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)