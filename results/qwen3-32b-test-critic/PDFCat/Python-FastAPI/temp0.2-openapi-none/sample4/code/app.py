from fastapi import FastAPI, File, UploadFile, HTTPException
import subprocess
import tempfile
import os
from fastapi.responses import Response

app = FastAPI()

@app.post("/concatenate")
async def concatenate_pdfs(files: list[UploadFile] = File(...)):
    if not files or len(files) < 1:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_paths = []
            for i, uploaded_file in enumerate(files):
                file_path = os.path.join(temp_dir, f"file_{i}.pdf")
                with open(file_path, "wb") as buffer:
                    buffer.write(await uploaded_file.read())
                file_paths.append(file_path)
            output_path = os.path.join(temp_dir, "concatenated.pdf")
            command = ["pdfunite"] + file_paths + [output_path]
            subprocess.run(command, check=True)
            with open(output_path, "rb") as output_file:
                pdf_data = output_file.read()
        return Response(content=pdf_data, media_type="application/pdf")
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail="An error occurred while processing the files.")
    except Exception as e:
        raise HTTPException(status_code=500, detail="An error occurred while processing the files.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)