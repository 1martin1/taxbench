from fastapi import FastAPI, File, UploadFile, JSONResponse, Response
import subprocess
import tempfile
import os

app = FastAPI()

@app.post("/convert")
async def convert_pdf_to_text(file: UploadFile = File(...)):
    if file.content_type != "application/pdf":
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid file type. Must be a PDF."}
        )
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_pdf_path = os.path.join(temp_dir, "input.pdf")
        try:
            contents = await file.read()
            with open(temp_pdf_path, "wb") as temp_file:
                temp_file.write(contents)
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={"error": "Failed to process uploaded file"}
            )
        
        try:
            result = subprocess.run(
                ["pdftotext", temp_pdf_path, "-"],
                capture_output=True,
                text=True,
                check=True
            )
            return Response(content=result.stdout, media_type="text/plain")
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip() or "Conversion failed"
            return JSONResponse(
                status_code=500,
                content={"error": error_msg}
            )
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={"error": "Conversion failed"}
            )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)