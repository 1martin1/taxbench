import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Response
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
        width, height = map(int, targetSize.split('x'))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid targetSize format. Use WxH.")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        image_paths = []
        for i, image in enumerate(images):
            ext = image.filename.split('.')[-1].lower() if '.' in image.filename else 'jpg'
            if ext not in ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'tiff']:
                ext = 'jpg'
            temp_path = os.path.join(tmpdir, f"img{i}.{ext}")
            contents = await image.read()
            with open(temp_path, 'wb') as f:
                f.write(contents)
            image_paths.append(temp_path)
        
        if appendReverted:
            image_paths += image_paths[::-1]
        
        output_path = os.path.join(tmpdir, 'output.gif')
        command = [
            'convert',
            '-delay', str(delay),
            '-resize', f'{width}x{height}!',
            *image_paths,
            output_path
        ]
        
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        if result.returncode != 0:
            error_msg = result.stderr.decode().strip() or "Unknown error"
            raise HTTPException(status_code=500, detail=f"GIF creation failed: {error_msg}")
        
        with open(output_path, 'rb') as f:
            gif_data = f.read()
        
        return Response(content=gif_data, media_type='image/gif')

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)