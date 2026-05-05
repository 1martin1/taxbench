import subprocess
import tempfile
import os
import shutil
from fastapi import FastAPI, UploadFile, File, HTTPException, status
from fastapi.responses import Response, JSONResponse
from typing import List

app = FastAPI()

@app.post("/concatenate")
async def concatenate_pdfs(files: List[UploadFile] = File(...)):
    if not files:
        return JSONResponse(status_code=400, content={"error": "Invalid input or missing files."})
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_files = []
            for i, file in enumerate(files):
                content = await file.read()
                if not content:
                    raise HTTPException(status_code=400, detail="Empty file uploaded.")
                if not content.startswith(b'%PDF-'):
                    raise HTTPException(status_code=400, detail="Invalid PDF file detected.")
                temp_file_path = os.path.join(temp_dir, f"temp_file_{i}.pdf")
                with open(temp_file_path, "wb") as temp_file:
                    temp_file.write(content)
                input_files.append(temp_file_path)
            output_path = os.path.join(temp_dir, "concatenated.pdf")
            command = ["pdfunite"] + input_files + [output_path]
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            with open(output_path, "rb") as output_file:
                pdf_data = output_file.read()
            return Response(content=pdf_data, media_type="application/pdf")
    except subprocess.CalledProcessError:
        return JSONResponse(status_code=500, content={"error": "An error occurred while processing the files."})
    except Exception:
        return JSONResponse(status_code=500, content={"error": "An error occurred while processing the files."})

if __name__ == "__main__":
    import uvicorn
    if not shutil.which("pdfunite"):
        print("Error: pdfunite is not installed. Please install poppler-utils.")
        exit(1)
    uvicorn.run(app, host="0.0.0.0", port=5000)