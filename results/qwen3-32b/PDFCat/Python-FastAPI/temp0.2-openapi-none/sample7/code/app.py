from fastapi import FastAPI, File, UploadFile, HTTPException, Response
import subprocess
import os
import tempfile
from typing import List

app = FastAPI()

@app.post("/concatenate")
async def concatenate_pdfs(files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")
    
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_files = []
            for file in files:
                file_path = os.path.join(temp_dir, file.filename)
                with open(file_path, "wb") as f:
                    content = await file.read()
                    f.write(content)
                input_files.append(file_path)
            
            output_path = os.path.join(temp_dir, "concatenated.pdf")
            command = ["pdfunite"] + input_files + [output_path]
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            
            if not os.path.exists(output_path):
                raise HTTPException(status_code=500, detail="Failed to generate concatenated PDF.")
            
            with open(output_path, "rb") as f:
                pdf_data = f.read()
            
            return Response(content=pdf_data, media_type="application/pdf")
    
    except subprocess.CalledProcessError as e:
        error_detail = "An error occurred while processing the files."
        if e.stderr:
            error_detail += f" Error: {e.stderr}"
        raise HTTPException(status_code=500, detail=error_detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail="An error occurred while processing the files.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)