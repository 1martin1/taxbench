import os
import subprocess
import tempfile
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Response
from typing import List

app = FastAPI()

@app.post("/create-gif")
async def create_gif(
    images: List[UploadFile] = File(...),
    targetSize: str = Form(...),
    delay: int = Form(10),
    appendReverted: bool = Form(False)
):
    try:
        width, height = map(int, targetSize.split('x'))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid targetSize format. Use 'widthxheight'.")

    with tempfile.TemporaryDirectory() as temp_dir:
        image_paths = []
        for i, image in enumerate(images):
            contents = await image.read()
            ext = os.path.splitext(image.filename)[1]
            if not ext:
                ext = ".png"
            temp_file = os.path.join(temp_dir, f"img_{i}{ext}")
            with open(temp_file, "wb") as buffer:
                buffer.write(contents)
            image_paths.append(temp_file)

        if appendReverted:
            image_paths += image_paths[::-1]

        output_path = os.path.join(temp_dir, "output.gif")
        command = [
            'convert',
            '-delay', str(delay),
            '-resize', f'{width}x{height}',
            *image_paths,
            output_path
        ]

        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as e:
            raise HTTPException(status_code=500, detail="Failed to create GIF using ImageMagick.")

        if not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail="GIF output file not found.")

        with open(output_path, "rb") as f:
            gif_data = f.read()

        return Response(content=gif_data, media_type="image/gif")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)