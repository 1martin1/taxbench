from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Response
import subprocess
import tempfile
import os
import re

app = FastAPI()

@app.post("/create-gif")
async def create_gif(
    images: list[UploadFile] = File(...),
    targetSize: str = Form(...),
    delay: int = Form(10),
    appendReverted: bool = Form(False)
):
    # Validate targetSize format
    if not re.match(r"^\d+x\d+$", targetSize):
        raise HTTPException(status_code=400, detail="Invalid targetSize format. Use 'WIDTHxHEIGHT'.")
    width, height = targetSize.split("x")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        image_paths = []
        for img in images:
            img_path = os.path.join(tmpdir, img.filename)
            with open(img_path, "wb") as buffer:
                buffer.write(await img.read())
            image_paths.append(img_path)
        
        if appendReverted:
            image_paths += image_paths[::-1]
        
        output_path = os.path.join(tmpdir, "output.gif")
        command = [
            "convert",
            "-delay", str(delay),
            "-resize", f"{width}x{height}",
            *image_paths,
            output_path
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else "Unknown error"
            raise HTTPException(status_code=500, detail=f"Image conversion failed: {error_msg}")
        
        with open(output_path, "rb") as f:
            gif_data = f.read()
        
        return Response(content=gif_data, media_type="image/gif")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)