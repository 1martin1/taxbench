from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from pathlib import Path
import re
import logging

# ---------------------------------------------------------------------------
# Configuration & Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="File Search API",
    description="API for searching files based on matching a given regex against their content.",
    version="1.0.0",
)

# Root directory where all searchable files are stored.
# Ensure it exists so that a missing directory does not cause a 400 error.
ROOT_DIR = Path(__file__).parent / "files"
ROOT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class SearchResponse(BaseModel):
    files: List[str]


class ErrorResponse(BaseModel):
    error: str


# ---------------------------------------------------------------------------
# Exception handling
# ---------------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Return errors in the format required by the OpenAPI schema."""
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def _ensure_within_root(target: Path) -> Path:
    """
    Resolve *target* and verify that it is located inside ROOT_DIR.
    Raises HTTPException(400) if the check fails.
    """
    try:
        resolved_target = target.resolve()
        # pathlib's relative_to raises ValueError if not a subpath
        resolved_target.relative_to(ROOT_DIR.resolve())
        return resolved_target
    except (ValueError, RuntimeError):
        raise HTTPException(status_code=400, detail="Directory traversal is not allowed.")
    except Exception as e:
        logger.exception("Unexpected error while validating directory.")
        raise HTTPException(status_code=400, detail="Invalid directory parameter.")


def _compile_regex(pattern: str) -> re.Pattern:
    """
    Compile a regular expression safely.
    Limits pattern length to mitigate ReDoS attacks.
    """
    MAX_REGEX_LENGTH = 500
    if len(pattern) > MAX_REGEX_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Regex pattern is too long (max {MAX_REGEX_LENGTH} characters).",
        )
    try:
        return re.compile(pattern)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex pattern: {e}")


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@app.get(
    "/search",
    response_model=SearchResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
def search_files(
    regex: str = Query(
        ...,
        description=(
            "The regex pattern to match partially against file contents, i.e. some part "
            "of the file content must match the regex."
        ),
        examples={"example": {"value": r"hello\s(you|world)"}},
    ),
    directory: Optional[str] = Query(
        None,
        description=(
            "The directory path to search files in, relative to the files root directory."
        ),
    ),
    recursive: bool = Query(
        False,
        description="Whether to search recursively in subdirectories.",
    ),
):
    """
    Search files under the given directory (relative to the root `./files`) and return
    paths of files whose content matches the supplied regular expression.
    """
    # -----------------------------------------------------------------------
    # Resolve target directory safely
    # -----------------------------------------------------------------------
    try:
        target_dir = ROOT_DIR if directory is None else ROOT_DIR / directory
        target_dir = _ensure_within_root(target_dir)

        if not target_dir.is_dir():
            raise HTTPException(
                status_code=400,
                detail="Specified directory does not exist or is not a directory.",
            )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error while processing directory parameter.")
        raise HTTPException(status_code=400, detail="Invalid directory parameter.")

    # -----------------------------------------------------------------------
    # Compile regex with safety checks
    # -----------------------------------------------------------------------
    pattern = _compile_regex(regex)

    # -----------------------------------------------------------------------
    # Search files
    # -----------------------------------------------------------------------
    matched_files: List[str] = []
    try:
        iterator = target_dir.rglob("*") if recursive else target_dir.glob("*")
        for path in iterator:
            if not path.is_file():
                continue

            try:
                # Read file content as text, ignoring decode errors.
                # Limit read size to avoid loading huge files into memory.
                MAX_READ_BYTES = 5 * 1024 * 1024  # 5 MB per file
                content_bytes = path.read_bytes()
                if len(content_bytes) > MAX_READ_BYTES:
                    continue  # Skip overly large files
                content = content_bytes.decode(errors="ignore")
            except Exception:
                # If a file cannot be read, skip it silently.
                continue

            if pattern.search(content):
                rel_path = path.relative_to(target_dir).as_posix()
                matched_files.append(rel_path)
    except Exception:
        logger.exception("Unexpected error during file search.")
        raise HTTPException(status_code=500, detail="Internal server error.")

    return SearchResponse(files=matched_files)


# ---------------------------------------------------------------------------
# Run the application
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)