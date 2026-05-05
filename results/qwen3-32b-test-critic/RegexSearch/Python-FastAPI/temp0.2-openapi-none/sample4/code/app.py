from fastapi import FastAPI, HTTPException
import re
from pathlib import Path

app = FastAPI()

FILES_ROOT = Path("files").resolve()

@app.get("/search")
async def search_files(
    regex: str,
    directory: str = None,
    recursive: bool = False,
):
    try:
        # Validate and construct search path
        if directory is None:
            search_path = FILES_ROOT
        else:
            potential_path = (FILES_ROOT / directory).resolve()
            if not potential_path.is_relative_to(FILES_ROOT):
                raise ValueError("Directory traversal attempt detected.")
            search_path = potential_path

        # Check if directory exists and is a directory
        if not search_path.exists():
            raise ValueError("Directory does not exist.")
        if not search_path.is_dir():
            raise ValueError("Path is not a directory.")
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Compile regex
    try:
        pattern = re.compile(regex)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {str(e)}")

    # Collect and process files
    try:
        files_to_check = []
        if recursive:
            for f in search_path.rglob("*"):
                if f.is_file():
                    files_to_check.append(f)
        else:
            for f in search_path.iterdir():
                if f.is_file():
                    files_to_check.append(f)

        matching_files = []
        for file_path in files_to_check:
            try:
                with file_path.open("r", encoding="utf-8") as f:
                    content = f.read()
                if pattern.search(content):
                    relative_path = file_path.relative_to(search_path)
                    matching_files.append(str(relative_path))
            except (IOError, UnicodeDecodeError):
                continue  # Skip unreadable/undecodable files

        return {"files": matching_files}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error during file processing.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=12356)