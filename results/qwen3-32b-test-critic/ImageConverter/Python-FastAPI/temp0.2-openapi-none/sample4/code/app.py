from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Response
import subprocess
import tempfile
import os

app = FastAPI()

@app.post("/create-gif")
async def create_gif(
    images: list[UploadFile] = File(...),
    targetSize: str = Form(...),
    delay: int = Form(10),
    appendReverted: bool = Form(False)
):
    try:
        width_str, height_str = targetSize.split('x')
        width = int(width_str)
        height = int(height_str)
        if width <= 0 or height <= 0:
            raise ValueError("Width and height must be positive integers.")
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid targetSize format. Use positive integers for WIDTHxHEIGHT (e.g., 500x500).")

    with tempfile.TemporaryDirectory() as temp_dir:
        image_paths = []
        for i, image in enumerate(images):
            file_ext = os.path.splitext(image.filename)[1] or ".tmp"
            file_path = os.path.join(temp_dir, f"img_{i}{file_ext}")
            with open(file_path, "wb") as buffer:
                buffer.write(await image.read())
            image_paths.append(file_path)

        resized_paths = []
        for path in image_paths:
            resized_path = os.path.join(temp_dir, f"resized_{os.path.basename(path)}")
            try:
                subprocess.run(
                    ["convert", path, "-resize", f"{width}x{height}!", resized_path],
                    check=True
                )
                resized_paths.append(resized_path)
            except subprocess.CalledProcessError as e:
                raise HTTPException(status_code=500, detail=f"Image resizing failed: {str(e)}")

        if appendReverted:
            resized_paths += list(reversed(resized_paths))

        output_path = os.path.join(temp_dir, "output.gif")
        try:
            subprocess.run(
                ["convert", "-delay", str(delay), *resized_paths, "-loop", "0", output_path],
                check=True
            )
        except subprocess.CalledProcessError as e:
            raise HTTPException(status_code=500, detail=f"GIF creation failed: {str(e)}")

        with open(output_path, "rb") as f:
            return Response(content=f.read(), media_type="image/gif")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)