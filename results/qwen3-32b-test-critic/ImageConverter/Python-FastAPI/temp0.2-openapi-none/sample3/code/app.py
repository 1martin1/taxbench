import os
import shutil
import tempfile
import subprocess
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse
import uvicorn

app = FastAPI()

@app.post("/create-gif")
async def create_gif(
    images: list[UploadFile] = File(...),
    targetSize: str = Form(...),
    delay: int = Form(10),
    appendReverted: bool = Form(False)
):
    # Validate targetSize format
    parts = targetSize.split('x')
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise HTTPException(status_code=400, detail="Invalid targetSize format. Use 'WxH'.")
    width, height = parts

    tmpdir = tempfile.mkdtemp()
    try:
        image_paths = []
        for i, image in enumerate(images):
            original_filename = image.filename or f"image_{i}"
            ext = os.path.splitext(original_filename)[1]
            if not ext:
                ext = ".png"
            file_path = os.path.join(tmpdir, f"image_{i}{ext}")
            with open(file_path, "wb") as f:
                content = await image.read()
                f.write(content)
            image_paths.append(file_path)

        if appendReverted:
            reversed_paths = image_paths[::-1]
            image_paths.extend(reversed_paths)

        output_path = os.path.join(tmpdir, "output.gif")
        cmd = [
            "convert",
            "-delay", str(delay),
            "-resize", f"{width}x{height}",
            *image_paths,
            "-loop", "0",
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            error_msg = result.stderr or "Unknown error"
            raise HTTPException(status_code=500, detail=f"GIF creation failed: {error_msg}")

        if not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail="Output GIF not found")

        return FileResponse(output_path, media_type="image/gif", filename="output.gif")
    finally:
        shutil.rmtree(tmpdir)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)