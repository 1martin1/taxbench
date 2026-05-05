from fastapi import FastAPI, HTTPException, Query
from pathlib import Path
import re
import os

app = FastAPI()

def is_dangerous_regex(pattern: str) -> bool:
    """Check for potentially dangerous regex patterns that could cause ReDoS."""
    # Check for nested quantifiers and other problematic patterns
    dangerous_patterns = [
        r'\$\s*[^)]*?[+*?{}]\s*[^)]*?\$\s*[+*?{}]',  # Nested quantifiers in groups
        r'\$\s*[^)]*?\$\s*[+*?{}]',  # Groups followed by quantifiers
        r'[+*?{}]\s*[+*?{}]',  # Repeated quantifiers (e.g., a**)
        r'\$\s*[^)]*?\$\s*\{',  # Groups with {m,n} quantifiers
    ]
    for dp in dangerous_patterns:
        if re.search(dp, pattern, re.DOTALL):
            return True
    return False

@app.get("/search")
async def search(
    regex: str,
    directory: str = Query(None),
    recursive: bool = Query(False)
):
    # Validate regex for security risks
    try:
        if is_dangerous_regex(regex):
            raise HTTPException(status_code=400, detail="Regex contains potentially dangerous patterns")
        pattern = re.compile(regex, timeout=1)  # Add timeout to prevent long compilation
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {e}")

    # Determine base directory
    base_dir = Path("files").resolve()
    if directory is None:
        directory = ""
    target_dir = (base_dir / directory).resolve()

    # Check for path traversal attempts
    if not target_dir.is_relative_to(base_dir):
        raise HTTPException(status_code=400, detail="Invalid directory path")

    # Check if directory exists and is accessible
    if not target_dir.exists() or not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="Directory not found or not accessible")

    # Collect files to check
    files_to_check = []
    if recursive:
        for root, _, files in os.walk(target_dir):
            for file in files:
                files_to_check.append(Path(root) / file)
    else:
        for file in target_dir.iterdir():
            if file.is_file():
                files_to_check.append(file)

    # Check each file against regex
    matched_files = []
    for file_path in files_to_check:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            if pattern.search(content):
                relative_path = file_path.relative_to(target_dir)
                matched_files.append(str(relative_path))
        except Exception:
            # Skip files that can't be read (per OpenAPI spec)
            pass

    return {"files": matched_files}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)