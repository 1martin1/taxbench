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
        # Validate delay
        if delay < 0:
            raise HTTPException(status_code=400, detail="Delay must be a non-negative integer.")
        
        # Parse targetSize
        try:
            width, height = map(int, targetSize.split('x'))
            if width <= 0 or height <= 0:
                raise HTTPException(status_code=400, detail="Width and height must be positive integers.")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid targetSize format. Use 'WIDTHxHEIGHT'.")

        # Create temporary directory
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save uploaded images
            image_paths = []
            for i, image in enumerate(images):
                # Use original filename's extension
                ext = os.path.splitext(image.filename)[1]
                file_path = os.path.join(tmpdir, f"img_{i}{ext}")
                with open(file_path, "wb") as f:
                    f.write(await image.read())
                image_paths.append(file_path)

            # Resize images
            resized_paths = []
            for idx, path in enumerate(image_paths):
                resized_path = os.path.join(tmpdir, f"resized_{idx}.png")
                subprocess.run(["convert", path, "-resize", f"{width}x{height}!", resized_path], check=True)
                resized_paths.append(resized_path)

            # Append reversed images if needed
            if appendReverted:
                reversed_paths = list(reversed(resized_paths))
                resized_paths.extend(reversed_paths)

            # Create GIF
            output_path = os.path.join(tmpdir, "output.gif")
            subprocess.run(["convert", "-delay", str(delay), *resized_paths, output_path], check=True)

            # Read the output file
            with open(output_path, "rb") as f:
                gif_data = f.read()

            return Response(content=gif_data, media_type="image/gif")

    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Image processing failed: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)