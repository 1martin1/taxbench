import os
import subprocess
import tempfile
import resource
import signal

from fastapi import FastAPI, File, HTTPException, UploadFile, Response, status

app = FastAPI(
    title="PDF to Text Converter API",
    description="A simple API to convert PDF files to Text format.",
    version="1.0.0",
)

# Maximum allowed upload size (10 MiB)
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
# Subprocess timeout (seconds)
SUBPROCESS_TIMEOUT = 30
# Resource limits for the conversion subprocess
CPU_TIME_LIMIT = 30               # seconds
MEMORY_LIMIT = 200 * 1024 * 1024  # 200 MiB


def _limit_resources():
    """
    Apply resource limits to the child process.
    Executed in the child process just before `pdftotext` runs.
    """
    # Limit CPU time
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_TIME_LIMIT, CPU_TIME_LIMIT))
    # Limit address space (virtual memory)
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))
    # Ensure the process receives SIGKILL if it exceeds CPU time
    signal.signal(signal.SIGXCPU, signal.SIG_DFL)


@app.post(
    "/convert",
    responses={
        200: {
            "description": "Successfully converted PDF to text",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        },
        400: {"description": "Bad Request - Invalid input or missing file"},
        500: {"description": "Internal Server Error - Conversion failed"},
    },
    summary="Convert a PDF file to Text",
    description="Upload a PDF file to convert it into Text format.",
)
def convert(file: UploadFile = File(...)):
    """
    Convert an uploaded PDF file to plain text using the `pdftotext` utility.
    """
    # Basic validation
    if not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file uploaded.",
        )
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type. Only PDF files are accepted.",
        )

    # Store uploaded PDF in a temporary file while enforcing size limit
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_input:
            total_bytes = 0
            while True:
                chunk = file.file.read(1024 * 1024)  # 1 MiB per iteration
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Uploaded file exceeds the size limit of 10 MiB.",
                    )
                tmp_input.write(chunk)
            tmp_input.flush()
            tmp_path = tmp_input.name
    except HTTPException:
        # Propagate size‑limit error unchanged
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process the uploaded file.",
        )

    # Execute `pdftotext` with resource limits and timeout
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", tmp_path, "-"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
            preexec_fn=_limit_resources,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PDF conversion timed out.",
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Conversion tool not available on the server.",
        )
    finally:
        # Ensure temporary file removal
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if result.returncode != 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PDF conversion failed.",
        )

    return Response(content=result.stdout, media_type="text/plain")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)