from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Response
import tempfile
import os
import subprocess
from typing import List

app = FastAPI()

@app.post("/create-gif")
async def create_gif(
    images: List[UploadFile] = File(...),
    targetSize: str = Form(...),
    delay: int = Form(10),
    appendReverted: bool = Form(False)
):
    if not images:
        raise HTTPException(status_code=400, detail="No images provided")
    
    try:
        width, height = map(int, targetSize.split('x'))
        if width <= 0 or height <= 0:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid targetSize format. Use WxH with positive integers.")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_image_paths = []
        for i, image in enumerate(images):
            ext = os.path.splitext(image.filename)[1] or ".jpg"
            temp_path = os.path.join(temp_dir, f"img_{i}{ext}")
            try:
                content = await image.read()
                with open(temp_path, "wb") as f:
                    f.write(content)
                temp_image_paths.append(temp_path)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Error saving image {i}: {str(e)}")
        
        resized_paths = []
        for idx, img_path in enumerate(temp_image_paths):
            resized_path = os.path.join(temp_dir, f"resized_{idx}.png")
            try:
                subprocess.run([
                    "convert",
                    img_path,
                    "-resize", f"{width}x{height}",
                    resized_path
                ], check=True)
                resized_paths.append(resized_path)
            except subprocess.CalledProcessError as e:
                raise HTTPException(status_code=500, detail=f"Error resizing image {idx}: {str(e)}")
        
        if appendReverted:
            reversed_paths = list(reversed(resized_paths))
            resized_paths += reversed_paths
        
        gif_path = os.path.join(temp_dir, "output.gif")
        try:
            subprocess.run([
                "convert",
                "-delay", str(delay),
                *resized_paths,
                gif_path
            ], check=True)
        except subprocess.CalledProcessError as e:
            raise HTTPException(status_code=500, detail=f"Error creating GIF: {str(e)}")
        
        if not os.path.exists(gif_path) or os.path.getsize(gif_path) == 0:
            raise HTTPException(status_code=500, detail="Generated GIF is empty")
        
        with open(gif_path, "rb") as f:
            gif_data = f.read()
        
        return Response(content=gif_data, media_type="image/gif")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)