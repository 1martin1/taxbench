import os
import subprocess
import tempfile
from fastapi import FastAPI, File, UploadFile, HTTPException, Response
from typing import List

app = FastAPI()

@app.post("/concatenate")
async def concatenate_pdfs(files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")
    with tempfile.TemporaryDirectory() as tmpdir:
        file_paths = []
        for i, file in enumerate(files):
            file_path = os.path.join(tmpdir, f"file_{i}.pdf")
            contents = await file.read()
            with open(file_path, "wb") as f:
                f.write(contents)
            file_paths.append(file_path)
        output_path = os.path.join(tmpdir, "concatenated.pdf")
        command = ["pdfunite"] + file_paths + [output_path]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail="An error occurred while processing the files.")
        with open(output_path, "rb") as f:
            pdf_data = f.read()
    return Response(content=pdf_data, media_type="application/pdf")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)