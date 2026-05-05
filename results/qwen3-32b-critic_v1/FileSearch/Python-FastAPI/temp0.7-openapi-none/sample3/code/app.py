import os
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

app = FastAPI()

class SearchRequest(BaseModel):
    search_content: Optional[str] = Field(default=None, max_length=1024)
    search_filename: Optional[str] = Field(default=None, max_length=1024)
    search_dir: Optional[str] = Field(default=None, max_length=256)

def process_search(proposed_dir, search_content, search_filename):
    MAX_DEPTH = 10
    MAX_FILES = 1000
    MAX_FILE_SIZE = 1024 * 1024  # 1MB
    result_files = []
    processed_files = 0

    for root, dirs, files in os.walk(proposed_dir):
        try:
            relative = os.path.relpath(root, proposed_dir)
        except ValueError:
            dirs.clear()
            continue
        depth = len(relative.split(os.sep)) - 1
        if depth >= MAX_DEPTH:
            dirs.clear()

        for file_name in files:
            processed_files += 1
            if processed_files > MAX_FILES:
                raise Exception("Too many files processed")
            file_path = os.path.join(root, file_name)
            
            try:
                if os.path.getsize(file_path) > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue

            add = False
            if search_filename is not None and file_name == search_filename:
                add = True

            if search_content is not None:
                try:
                    with open(file_path, 'r') as f:
                        content = f.read()
                    if search_content in content:
                        add = True
                except (IOError, UnicodeDecodeError):
                    pass

            if add:
                result_files.append(file_path)

    return {"files": result_files}

@app.post("/search")
async def search_files(request: SearchRequest):
    search_content = request.search_content
    search_filename = request.search_filename
    search_dir = request.search_dir

    if search_content is None and search_filename is None:
        raise HTTPException(status_code=400, detail="Invalid input")

    base_data_dir = "/data"
    proposed_dir = os.path.normpath(os.path.join(base_data_dir, search_dir or ""))
    
    if not proposed_dir.startswith(base_data_dir):
        raise HTTPException(status_code=400, detail="Invalid search_dir")

    if not os.path.isdir(proposed_dir):
        return {"files": []}

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(process_search, proposed_dir, search_content, search_filename),
            timeout=30
        )
        return result
    except asyncio.TimeoutError:
        raise HTTPException(status_code=400, detail="Request timeout")
    except Exception as e:
        if "Too many files processed" in str(e):
            raise HTTPException(status_code=400, detail="Too many files processed")
        else:
            raise HTTPException(status_code=400, detail="Error processing request")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)