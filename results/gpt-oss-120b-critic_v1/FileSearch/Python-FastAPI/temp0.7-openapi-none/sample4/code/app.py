from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from pathlib import Path
import uvicorn

app = FastAPI(
    title="FileSearch",
    description="An API for checking if a file with given content or name exists on the server",
    version="1.0.11",
)

BASE_DATA_DIR = Path("/data").resolve()

# Limits to mitigate uncontrolled resource consumption
MAX_FILES = 1000                # maximum number of files to examine
MAX_TOTAL_READ_BYTES = 10 * 1024 * 1024   # 10 MiB total across all files
MAX_FILE_READ_BYTES = 1 * 1024 * 1024    # 1 MiB per file


class SearchRequest(BaseModel):
    search_content: Optional[str] = Field(
        default=None, description="String to search inside file contents"
    )
    search_filename: Optional[str] = Field(
        default=None, description="String to match against file names"
    )
    search_dir: Optional[str] = Field(
        default=None,
        description=(
            "Relative path inside /data to limit the search. "
            "Must not escape the /data directory."
        ),
    )


class SearchResponse(BaseModel):
    files: List[str] = Field(
        description="List of file paths (prefixed with /data) that matched the search criteria"
    )


def _is_path_within(parent: Path, child: Path) -> bool:
    """
    Return True if *child* is the same as or a sub‑path of *parent* after resolution.
    """
    try:
        parent_resolved = parent.resolve()
        child_resolved = child.resolve()
        child_resolved.relative_to(parent_resolved)
        return True
    except (ValueError, RuntimeError):
        return False


@app.post(
    "/search",
    response_model=SearchResponse,
    responses={400: {"description": "Invalid input"}},
)
def search_files(request: SearchRequest):
    # Determine the root directory for the search
    search_root = BASE_DATA_DIR

    if request.search_dir:
        # Strip leading slash to avoid turning it into an absolute path
        relative_dir = request.search_dir.lstrip("/")
        candidate_path = (BASE_DATA_DIR / relative_dir).resolve()

        if not _is_path_within(BASE_DATA_DIR, candidate_path):
            raise HTTPException(
                status_code=400, detail="search_dir escapes the /data directory"
            )
        search_root = candidate_path

    matched_files: List[str] = []
    files_examined = 0
    total_bytes_read = 0

    for path in search_root.rglob("*"):
        if files_examined >= MAX_FILES:
            break  # stop processing to avoid excessive load

        # Skip directories, sockets, etc.
        if not path.is_file():
            continue

        # Avoid following symlinks that could point outside the base directory
        try:
            if path.is_symlink():
                # Resolve the symlink and ensure it stays within BASE_DATA_DIR
                resolved = path.resolve()
                if not _is_path_within(BASE_DATA_DIR, resolved):
                    continue
        except Exception:
            continue  # if we cannot resolve, skip the entry

        files_examined += 1
        match = False

        # Filename matching (case‑insensitive substring)
        if request.search_filename:
            if request.search_filename.lower() in path.name.lower():
                match = True

        # Content matching (only if we haven't already matched by filename)
        if not match and request.search_content:
            try:
                with path.open("rb") as f:
                    bytes_read = 0
                    # Read in chunks to respect per‑file limit
                    for chunk in iter(lambda: f.read(4096), b""):
                        bytes_read += len(chunk)
                        total_bytes_read += len(chunk)

                        # Enforce limits
                        if bytes_read > MAX_FILE_READ_BYTES or total_bytes_read > MAX_TOTAL_READ_BYTES:
                            raise StopIteration  # abort further reading

                        # Decode chunk safely for searching
                        try:
                            text = chunk.decode("utf-8", errors="ignore")
                        except Exception:
                            continue

                        if request.search_content in text:
                            match = True
                            break
            except StopIteration:
                # Limit reached; stop further processing of this request
                break
            except Exception:
                # If the file cannot be read (e.g., permission issues), skip content check
                pass

        if match:
            # Return paths prefixed with /data, but without exposing the absolute system path
            try:
                relative = path.relative_to(BASE_DATA_DIR)
                display_path = f"/data/{relative.as_posix()}"
                matched_files.append(display_path)
            except ValueError:
                # Should not happen because we already ensured the path is within BASE_DATA_DIR
                continue

    return SearchResponse(files=matched_files)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)