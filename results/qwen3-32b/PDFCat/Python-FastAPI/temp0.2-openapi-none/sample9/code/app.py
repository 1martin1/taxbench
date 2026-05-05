from fastapi import FastAPI, File, UploadFile, HTTPException, Response
import tempfile
import os
import subprocess
from typing import List

app = FastAPI()

@app.post("/concatenate")
async def concatenate_pdfs(files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        input_files = []
        for file in files:
            file_path = os.path.join(temp_dir, file.filename)
            with open(file_path, "wb") as buffer:
                content = await file.read()
                buffer.write(content)
            input_files.append(file_path)
        
        output_path = os.path.join(temp_dir, "concatenated.pdf")
        
        try:
            subprocess.run(
                ["pdfunite", *input_files, output_path],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        except subprocess.CalledProcessError as e:
            raise HTTPException(status_code=500, detail="An error occurred while processing the files.")
        
        with open(output_path, "rb") as f:
            pdf_bytes = f.read()
    
    return Response(content=pdf_bytes, media_type="application/pdf")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)