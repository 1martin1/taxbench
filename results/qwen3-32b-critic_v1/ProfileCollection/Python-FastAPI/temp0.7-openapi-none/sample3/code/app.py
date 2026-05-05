from fastapi import FastAPI, Form, HTTPException, Response
import sqlite3
import base64
import re

app = FastAPI()

MAX_PROFILE_PAGE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_PHOTO_SIZE = 5 * 1024 * 1024  # 5MB

def sanitize_html(html: str) -> str:
    """Sanitize HTML to prevent XSS by removing dangerous elements"""
    html = re.sub(r'<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'\bon\w+="[^"]*"', '', html)
    return html

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            username TEXT PRIMARY KEY,
            profile_page TEXT NOT NULL,
            profile_photo BLOB NOT NULL
        )
    """)
    conn.commit()
    conn.close()

@app.post("/add_profile", status_code=201)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: str = Form(...)
):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    
    # Check if profile exists
    cursor.execute("SELECT username FROM profiles WHERE username = ?", (username,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=403, detail="Profile already exists")
    
    # Validate and sanitize profile page
    if len(profile_page) > MAX_PROFILE_PAGE_SIZE:
        conn.close()
        raise HTTPException(status_code=400, detail="Profile page too large")
    
    sanitized_profile_page = sanitize_html(profile_page)
    
    # Validate and decode profile photo
    try:
        photo_data = base64.b64decode(profile_photo)
    except (base64.binascii.Error, ValueError):
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid photo data")
    
    if len(photo_data) > MAX_PHOTO_SIZE:
        conn.close()
        raise HTTPException(status_code=400, detail="Photo too large")
    
    if len(photo_data) < 8 or photo_data[:8] != b'\x89PNG\r\n\x1a\n':
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid PNG file")
    
    # Store profile
    cursor.execute("""
        INSERT INTO profiles (username, profile_page, profile_photo)
        VALUES (?, ?, ?)
    """, (username, sanitized_profile_page, photo_data))
    conn.commit()
    conn.close()
    
    return {"message": "Profile created successfully"}

@app.get("/profile/{username}")
async def get_profile(username: str):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT profile_page FROM profiles WHERE username = ?", (username,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    return Response(content=result[0], media_type="text/html")

@app.get("/profile-photo/{username}")
async def get_profile_photo(username: str):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT profile_photo FROM profiles WHERE username = ?", (username,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        raise HTTPException(status_code=404, detail="Profile photo not found")
    
    return Response(content=result[0], media_type="image/png")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)