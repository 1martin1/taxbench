from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Response
import subprocess
import tempfile
import os

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
        raise HTTPException(status_code=400, detail="Invalid targetSize format. Use 'WIDTHxHEIGHT'.")

    with tempfile.TemporaryDirectory() as temp_dir:
        image_paths = []
        for image in images:
            try:
                contents = await image.read()
                if not contents:
                    raise HTTPException(status_code=400, detail="Uploaded file is empty.")
            except Exception as e:
                raise HTTPException(status_code=400, detail="Error reading image file.")
            temp_path = os.path.join(temp_dir, image.filename)
            try:
                with open(temp_path, "wb") as f:
                    f.write(contents)
            except IOError:
                raise HTTPException(status_code=500, detail="Failed to save uploaded image.")
            image_paths.append(temp_path)

        if appendReverted:
            image_paths = image_paths + image_paths[::-1]

        output_path = os.path.join(temp_dir, 'output.gif')

        command = [
            'convert',
            '-resize', f'{width}x{height}',
            '-delay', str(delay // 10),
            *image_paths,
            output_path
        ]

        try:
            subprocess.run(command, check=True, cwd=temp_dir)
        except subprocess.CalledProcessError as e:
            raise HTTPException(status_code=500, detail="Failed to generate GIF with ImageMagick.")

        if not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail="GIF output file not found.")

        with open(output_path, "rb") as f:
            gif_data = f.read()

        return Response(content=gif_data, media_type="image/gif")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)