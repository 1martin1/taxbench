from fastapi import FastAPI, File, UploadFile, Form, Response, HTTPException, Request
import sqlite3
import re
from datetime import datetime, timedelta

app = FastAPI()

# Global rate limiting variables
request_counter = {}
MAX_HTML_SIZE = 100000  # 100KB
MAX_PHOTO_SIZE = 1024 * 1024  # 1MB

def get_db():
    db = sqlite3.connect('db.sqlite3', check_same_thread=False)
    db.execute('''
        CREATE TABLE IF NOT EXISTS profiles (
            username TEXT PRIMARY KEY,
            profile_page TEXT NOT NULL,
            profile_photo BLOB NOT NULL
        )
    ''')
    return db

@app.post("/add_profile")
async def add_profile(
    request: Request,
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...)
):
    ip = request.client.host
    current_time = datetime.now()
    
    # Rate limiting: 10 requests per minute per IP
    if ip in request_counter:
        # Remove old entries
        request_counter[ip] = [t for t in request_counter[ip] if t > current_time - timedelta(minutes=1)]
        if len(request_counter[ip]) >= 10:
            raise HTTPException(status_code=429, detail="Too many requests")
    # Add current request
    request_counter[ip] = request_counter.get(ip, []) + [current_time]
    
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute("SELECT username FROM profiles WHERE username = ?", (username,))
        if cursor.fetchone():
            raise HTTPException(status_code=403, detail="Profile already exists")
        
        # Validate HTML size
        if len(profile_page) > MAX_HTML_SIZE:
            raise HTTPException(status_code=400, detail="Profile page too large")
        
        # Sanitize HTML (basic script removal)
        sanitized_html = re.sub(r'<script.*?</script>', '', profile_page, flags=re.DOTALL)
        
        # Validate photo
        photo_contents = await profile_photo.read()
        if len(photo_contents) > MAX_PHOTO_SIZE:
            raise HTTPException(status_code=400, detail="Profile photo too large")
        if not photo_contents.startswith(b'\x89PNG\r\n\x1a\n'):
            raise HTTPException(status_code=400, detail="Invalid PNG file")
        
        # Insert profile
        cursor.execute(
            "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
            (username, sanitized_html, photo_contents)
        )
        db.commit()
    except Exception as e:
        db.close()
        raise
    finally:
        db.close()
    return Response(status_code=201)

@app.get("/profile/{username}")
def get_profile(username: str):
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute("SELECT profile_page FROM profiles WHERE username = ?", (username,))
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Profile not found")
        return Response(content=result[0], media_type="text/html")
    finally:
        db.close()

@app.get("/profile-photo/{username}")
def get_profile_photo(username: str):
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute("SELECT profile_photo FROM profiles WHERE username = ?", (username,))
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Profile photo not found")
        return Response(content=result[0], media_type="image/png")
    finally:
        db.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)