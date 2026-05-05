from fastapi import FastAPI, HTTPException
import os
import re
import traceback

app = FastAPI()

@app.get("/search")
async def search_files(regex: str, directory: str = ".", recursive: bool = False):
    try:
        base_dir = os.path.abspath("files")
        user_dir = directory
        resolved_dir = os.path.abspath(os.path.join(base_dir, user_dir))
        
        # Validate directory is within allowed root
        if os.path.commonpath([base_dir, resolved_dir]) != base_dir:
            raise HTTPException(status_code=400, detail=f"Directory {directory} is outside the allowed root.")
        
        # Validate directory exists and is a directory
        if not os.path.exists(resolved_dir):
            raise HTTPException(status_code=400, detail=f"Directory {directory} does not exist.")
        if not os.path.isdir(resolved_dir):
            raise HTTPException(status_code=400, detail=f"{directory} is not a directory.")
        
        # Compile regex with timeout to prevent ReDoS
        try:
            pattern = re.compile(regex, timeout=1)
        except re.error as e:
            raise HTTPException(status_code=400, detail=f"Invalid regex: {e}")
        except Exception as e:
            raise HTTPException(status_code=400, detail="Regex compilation timeout")
        
        # Search files
        matching_files = []
        if recursive:
            for root, _, filenames in os.walk(resolved_dir, followlinks=False):
                for filename in filenames:
                    file_path = os.path.join(root, filename)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            if pattern.search(content):
                                rel_path = os.path.relpath(file_path, resolved_dir)
                                matching_files.append(rel_path)
                    except Exception:
                        pass
        else:
            for filename in os.listdir(resolved_dir):
                file_path = os.path.join(resolved_dir, filename)
                if os.path.isfile(file_path):
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            if pattern.search(content):
                                rel_path = os.path.relpath(file_path, resolved_dir)
                                matching_files.append(rel_path)
                    except Exception:
                        pass
        
        return {"files": matching_files}
    
    except HTTPException:
        raise
    except Exception as e:
        traceback_str = traceback.format_exc()
        print(traceback_str)
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)