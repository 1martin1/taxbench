from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Response
import os
import subprocess
import tempfile
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
        # Parse targetSize
        width, height = map(int, targetSize.split('x'))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid targetSize format. Use 'WIDTHxHEIGHT' with integers.")
    
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_paths = []
            for i, image in enumerate(images):
                # Save each image to a temporary file
                temp_path = os.path.join(temp_dir, f"image_{i}")
                # Read the uploaded file
                contents = await image.read()
                with open(temp_path, "wb") as temp_file:
                    temp_file.write(contents)
                image_paths.append(temp_path)
            
            # Append reversed images if needed
            if appendReverted:
                reversed_paths = list(reversed(image_paths))
                image_paths += reversed_paths
            
            # Prepare the output path
            output_path = os.path.join(temp_dir, "output.gif")
            
            # Build the convert command
            command = ["convert"]
            # Resize to target size
            command.extend(["-resize", f"{width}x{height}"])
            # Set delay (convert uses hundredths of a second)
            command.extend(["-delay", str(delay // 10)])
            # Add image paths
            command.extend(image_paths)
            # Output file
            command.append(output_path)
            
            # Execute the command
            try:
                result = subprocess.run(command, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as e:
                error_msg = e.stderr or "Unknown error"
                raise HTTPException(status_code=500, detail=f"Image conversion failed: {error_msg}")
            
            # Check if output file was created
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                raise HTTPException(status_code=500, detail="Generated GIF is empty")
            
            # Read and return the GIF
            with open(output_path, "rb") as gif_file:
                gif_data = gif_file.read()
            
            return Response(content=gif_data, media_type="image/gif")
    
    except Exception as e:
        # Catch any unexpected errors and return 500
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)