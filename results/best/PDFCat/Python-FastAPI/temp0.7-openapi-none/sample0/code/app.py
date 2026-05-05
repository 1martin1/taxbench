import subprocess
import tempfile
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, HTTPException, UploadFile, Response
import uvicorn

app = FastAPI(
    title="PDF Concatenation Service",
    description="A service that concatenates multiple PDF files into a single PDF.",
    version="1.0.0",
)


@app.post(
    "/concatenate",
    responses={
        200: {
            "description": "Successfully concatenated the PDF files.",
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
        },
        400: {
            "description": "Bad Request - Invalid input.",
            "content": {"application/json": {"example": {"error": "File must be a PDF."}}},
        },
        422: {
            "description": "Validation Error - Missing files field.",
            "content": {"application/json": {"example": {"error": "Invalid input or missing files."}}},
        },
        500: {
            "description": "Internal Server Error - An error occurred while processing the files.",
            "content": {"application/json": {"example": {"error": "An error occurred while processing the files."}}},
        },
    },
)
async def concatenate(files: List[UploadFile] = File(...)):
    """
    Accept multiple PDF files and return a single concatenated PDF.
    """
    # FastAPI automatically returns 422 if files field is missing

    # Ensure all uploaded files are PDFs
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=400,
                detail={"error": f"File {f.filename} must be a PDF."}
            )

    # Check if all files have content
    for f in files:
        if not f.filename or f.filename == "":
            raise HTTPException(
                status_code=400,
                detail={"error": "All files must have valid filenames."}
            )

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            input_paths = []
            for idx, upload in enumerate(files):
                # Sanitize filename to avoid path traversal
                safe_filename = Path(upload.filename).name

                # Reject filenames with path separators
                if '/' in safe_filename or '\\' in safe_filename:
                    raise HTTPException(
                        status_code=400,
                        detail={"error": "Invalid filename containing path separators."}
                    )

                file_path = tmp_path / f"input_{idx}_{safe_filename}"
                content = await upload.read()

                if len(content) == 0:
                    raise HTTPException(
                        status_code=400,
                        detail={"error": f"File {safe_filename} is empty."}
                    )

                with file_path.open("wb") as out_file:
                    out_file.write(content)

                input_paths.append(str(file_path))

            output_path = tmp_path / "concatenated.pdf"

            # Run pdfunite command
            cmd = ["pdfunite"] + input_paths + [str(output_path)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode != 0:
                if "Could not merge damaged documents" in result.stderr:
                    raise HTTPException(
                        status_code=400,
                        detail={"error": "One or more files are not valid PDF documents."}
                    )
                else:
                    raise RuntimeError(f"pdfunite failed: {result.stderr}")

            if not output_path.exists() or output_path.stat().st_size == 0:
                raise RuntimeError("Output file was not created or is empty")

            with output_path.open("rb") as f:
                pdf_bytes = f.read()

            return Response(content=pdf_bytes, media_type="application/pdf")

    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=500,
            detail={"error": "Processing timeout."}
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": f"An error occurred while processing the files: {str(exc)}"}
        )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)