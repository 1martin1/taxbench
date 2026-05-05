from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
import tempfile
import os
import subprocess
from typing import List

app = FastAPI()

@app.post("/concatenate")
async def concatenate(files: List[UploadFile] = File(...)):
    if len(files) < 2:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")
    with tempfile.TemporaryDirectory() as tmpdir:
        input_paths = []
        for i, file in enumerate(files):
            file_path = os.path.join(tmpdir, f"file_{i}.pdf")
            try:
                content = await file.read()
                with open(file_path, "wb") as f:
                    f.write(content)
            except Exception as e:
                raise HTTPException(400, "Invalid input files.")
            input_paths.append(file_path)
        output_path = os.path.join(tmpdir, "output.pdf")
        try:
            subprocess.run(["pdfunite"] + input_paths + [output_path], check=True)
        except subprocess.CalledProcessError as e:
            raise HTTPException(500, "An error occurred while processing the files.")
        try:
            with open(output_path, "rb") as f:
                pdf_data = f.read()
            return Response(content=pdf_data, media_type="application/pdf")
        except Exception as e:
            raise HTTPException(500, "Failed to read output file.")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": "Invalid input or missing files."},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)