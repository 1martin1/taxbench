from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Response
import subprocess
import tempfile
import os
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
            try:
                content = await image.read()
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Error reading image {i}: {str(e)}")
            file_path = os.path.join(temp_dir, f"image_{i}")
            try:
                with open(file_path, "wb") as f:
                    f.write(content)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error saving image {i}: {str(e)}")
            image_paths.append(file_path)

        if appendReverted:
            image_files = image_paths + list(reversed(image_paths))
        else:
            image_files = image_paths

        output_path = os.path.join(temp_dir, 'output.gif')
        command = ['convert']
        command.extend(['-delay', str(delay // 10)])  # Convert ms to hundredths of a second
        command.extend(['-resize', f'{width}x{height}'])
        command.extend(image_files)
        command.append(output_path)

        try:
            result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode().strip()
            raise HTTPException(status_code=500, detail=f"ImageMagick error: {error_msg}")

        if not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail="GIF creation failed. Output file not found.")

        try:
            with open(output_path, 'rb') as f:
                gif_data = f.read()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error reading output GIF: {str(e)}")

        return Response(content=gif_data, media_type='image/gif')

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)