from fastapi import FastAPI, UploadFile, File, HTTPException
from typing import List
import subprocess
import tempfile
import os
from starlette.responses import Response

app = FastAPI()

@app.post("/concatenate")
async def concatenate_pdfs(files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_files = []
        try:
            for file in files:
                if not file.filename.endswith('.pdf'):
                    raise HTTPException(status_code=400, detail="All files must be PDFs.")
                temp_path = os.path.join(temp_dir, file.filename)
                with open(temp_path, "wb") as buffer:
                    content = await file.read()
                    buffer.write(content)
                temp_files.append(temp_path)
            
            if not temp_files:
                raise HTTPException(status_code=400, detail="Invalid input or missing files.")
            
            output_path = os.path.join(temp_dir, "concatenated.pdf")
            command = ['pdfunite'] + temp_files + [output_path]
            
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True
            )
            
            with open(output_path, "rb") as f:
                pdf_data = f.read()
            
            return Response(content=pdf_data, media_type="application/pdf")
        
        except HTTPException as he:
            raise he
        except subprocess.CalledProcessError as e:
            error_detail = e.stderr if e.stderr else "Unknown error"
            raise HTTPException(
                status_code=500,
                detail=f"An error occurred while processing the files: {error_detail}"
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail="An error occurred while processing the files"
            )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)