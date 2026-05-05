from fastapi import FastAPI, Form, HTTPException, Response, status
import sqlite3
import bleach
from html.parser import HTMLParser
import base64
import re

app = FastAPI()

# Username validation regex: alphanumeric, underscores, 1-32 chars
USERNAME_REGEX = re.compile(r'^\w{1,32}$')

class HTMLValidator(HTMLParser):
    def __init__(self):
        super().__init__()
        self.valid = True

    def error(self, message):
        self.valid = False

def is_valid_html(html: str) -> bool:
    validator = HTMLValidator()
    try:
        validator.feed(html)
        validator.close()
        return validator.valid
    except:
        return False

def is_valid_png(png_data: bytes) -> bool:
    if len(png_data) < 8:
        return False
    return png_data[:8] == b'\x89PNG\r\n\x1a\n'

@app.on_event("startup")
def startup():
    try:
        with sqlite3.connect('db.sqlite3') as db:
            db.execute('''
                CREATE TABLE IF NOT EXISTS profiles (
                    username TEXT PRIMARY KEY,
                    profile_html TEXT,
                    profile_photo BLOB
                )
            ''')
            db.commit()
    except sqlite3.Error as e:
        raise RuntimeError("Database initialization failed") from e

@app.post("/add_profile", status_code=201)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: str = Form(...)
):
    # Validate username
    if not USERNAME_REGEX.match(username):
        raise HTTPException(status_code=400, detail="Invalid username")
    
    # Sanitize and validate HTML
    sanitized_html = bleach.clean(profile_page, 
        tags=bleach.sanitizer.ALLOWED_TAGS + ['html', 'body', 'head', 'title'],
        attributes={}, protocols=[])
    
    if not is_valid_html(sanitized_html):
        raise HTTPException(status_code=400, detail="Invalid HTML content")
    
    # Decode and validate base64 PNG
    try:
        photo_bytes = base64.b64decode(profile_photo)
    except (base64.binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Invalid base64 data")
    
    if not is_valid_png(photo_bytes):
        raise HTTPException(status_code=400, detail="Invalid PNG format")
    
    # Check for existing profile
    try:
        with sqlite3.connect('db.sqlite3') as db:
            cursor = db.cursor()
            cursor.execute("SELECT 1 FROM profiles WHERE username = ?", (username,))
            if cursor.fetchone():
                raise HTTPException(status_code=403, detail="Profile already exists")
            
            cursor.execute(
                "INSERT INTO profiles (username, profile_html, profile_photo) VALUES (?, ?, ?)",
                (username, sanitized_html, photo_bytes)
            )
            db.commit()
    except sqlite3.OperationalError as e:
        raise HTTPException(status_code=503, detail="Database temporarily unavailable") from e
    
    return {"message": "Profile created successfully"}

@app.get("/profile/{username}")
async def get_profile(username: str):
    try:
        with sqlite3.connect('db.sqlite3') as db:
            cursor = db.cursor()
            cursor.execute("SELECT profile_html FROM profiles WHERE username = ?", (username,))
            result = cursor.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="Profile not found")
            return Response(content=result[0], media_type="text/html")
    except sqlite3.Error as e:
        raise HTTPException(status_code=503, detail="Database error") from e

@app.get("/profile-photo/{username}")
async def get_profile_photo(username: str):
    try:
        with sqlite3.connect('db.sqlite3') as db:
            cursor = db.cursor()
            cursor.execute("SELECT profile_photo FROM profiles WHERE username = ?", (username,))
            result = cursor.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="Profile photo not found")
            return Response(content=result[0], media_type="image/png")
    except sqlite3.Error as e:
        raise HTTPException(status_code=503, detail="Database error") from e

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)