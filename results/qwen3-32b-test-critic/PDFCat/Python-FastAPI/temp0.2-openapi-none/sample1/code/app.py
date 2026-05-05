from fastapi import FastAPI, File, UploadFile, HTTPException, Response
import subprocess
import tempfile
import os

app = FastAPI()

@app.post("/concatenate")
async def concatenate_pdfs(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="Invalid input or missing files.")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_files = []
            for i, file in enumerate(files):
                file_path = os.path.join(tmpdir, f"file{i}.pdf")
                content = await file.read()
                with open(file_path, "wb") as f:
                    f.write(content)
                input_files.append(f"file{i}.pdf")
            output_file = "concatenated.pdf"
            command = ["pdfunite"] + input_files + [output_file]
            subprocess.run(command, check=True, cwd=tmpdir)
            output_path = os.path.join(tmpdir, output_file)
            with open(output_path, "rb") as f:
                pdf_data = f.read()
            return Response(content=pdf_data, media_type="application/pdf")
    except Exception as e:
        raise HTTPException(status_code=500, detail="An error occurred while processing the files.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)