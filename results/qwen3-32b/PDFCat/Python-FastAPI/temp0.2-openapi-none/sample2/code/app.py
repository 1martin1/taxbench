import tempfile
import os
import subprocess
from fastapi import FastAPI, File, UploadFile, HTTPException, Response
from typing import List

app = FastAPI()

@app.post("/concatenate")
async def concatenate_pdfs(files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")
    
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_paths = []
            for file in files:
                with tempfile.NamedTemporaryFile(suffix='.pdf', dir=temp_dir, delete=False) as tmp:
                    content = await file.read()
                    tmp.write(content)
                    tmp_path = tmp.name
                input_paths.append(tmp_path)
            
            output_path = os.path.join(temp_dir, "concatenated.pdf")
            command = ['pdfunite'] + input_paths + [output_path]
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            with open(output_path, "rb") as f:
                pdf_data = f.read()
            
            return Response(content=pdf_data, media_type="application/pdf")
    
    except (subprocess.CalledProcessError, Exception):
        raise HTTPException(status_code=500, detail="An error occurred while processing the files.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)