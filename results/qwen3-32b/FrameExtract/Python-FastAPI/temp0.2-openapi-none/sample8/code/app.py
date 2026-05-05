from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse
import tempfile
import subprocess
import os

app = FastAPI()

@app.post("/extract")
async def extract_frame(
    videoFileName: str = Form(...),
    frameNumber: int = Form(...),
    video: UploadFile = File(...)
):
    with tempfile.TemporaryDirectory() as temp_dir:
        safe_filename = os.path.basename(videoFileName)
        input_path = os.path.join(temp_dir, safe_filename)
        output_path = os.path.join(temp_dir, "output.png")
        
        with open(input_path, "wb") as buffer:
            buffer.write(await video.read())
        
        command = [
            'ffmpeg',
            '-i', input_path,
            '-vf', f"select=eq(n\\){frameNumber}, shortest=1",
            '-vframes', '1',
            output_path,
            '-y',
            '-loglevel', 'quiet'
        ]
        
        subprocess.run(command, check=False)
        
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return FileResponse(output_path, media_type="image/png")
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Frame at index {frameNumber} could not be found."
            )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)