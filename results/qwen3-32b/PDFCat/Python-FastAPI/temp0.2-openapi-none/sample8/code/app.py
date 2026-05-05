from fastapi import FastAPI, File, UploadFile, Response, HTTPException
import subprocess
import tempfile
import os

app = FastAPI()

@app.post("/concatenate")
async def concatenate_pdfs(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            saved_files = []
            for i, file in enumerate(files):
                file_path = os.path.join(temp_dir, f"file_{i}.pdf")
                content = await file.read()
                if not content:
                    raise HTTPException(status_code=400, detail="One of the files is empty.")
                with open(file_path, "wb") as f:
                    f.write(content)
                saved_files.append(file_path)
            output_path = os.path.join(temp_dir, "concatenated.pdf")
            command = ["pdfunite"] + saved_files + [output_path]
            subprocess.run(command, check=True)
            with open(output_path, "rb") as f:
                pdf_bytes = f.read()
            return Response(content=pdf_bytes, media_type="application/pdf")
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail="An error occurred while processing the files.")
    except Exception as e:
        raise HTTPException(status_code=500, detail="An error occurred while processing the files.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)