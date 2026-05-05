import re
import sqlite3
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, status
from fastapi.responses import HTMLResponse, Response

app = FastAPI()

def is_valid_png(data: bytes) -> bool:
    return len(data) >= 8 and data[:8] == b'\x89PNG\r\n\x1a\n'

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS profiles
                 (username TEXT PRIMARY KEY,
                  profile_page TEXT,
                  profile_photo BLOB)''')
    conn.commit()
    conn.close()

@app.post("/add_profile", status_code=201)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(..., max_length=1024*1024),  # 1MB limit for HTML
    profile_photo: UploadFile = File(..., max_size=1024*1024*5)  # 5MB limit for PNG
):
    # Sanitize HTML to prevent XSS
    sanitized_html = re.sub(r'<script.*?</script>', '', profile_page, flags=re.DOTALL)
    sanitized_html = re.sub(r' on[a-zA-Z]+="[^"]*"', '', sanitized_html)
    sanitized_html = re.sub(r'<!--.*?-->', '', sanitized_html, flags=re.DOTALL)
    
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT username FROM profiles WHERE username = ?", (username,))
    if c.fetchone():
        conn.close()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Profile already exists")
    
    photo_bytes = await profile_photo.read()
    if not is_valid_png(photo_bytes):
        conn.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid PNG file")
    
    try:
        c.execute("INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
                  (username, sanitized_html, sqlite3.Binary(photo_bytes)))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Profile already exists")
    finally:
        conn.close()
    
    return {"message": "Profile created successfully"}

@app.get("/profile/{username}")
async def get_profile(username: str):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT profile_page FROM profiles WHERE username = ?", (username,))
    result = c.fetchone()
    conn.close()
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return HTMLResponse(content=result[0], status_code=status.HTTP_200_OK)

@app.get("/profile-photo/{username}")
async def get_profile_photo(username: str):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT profile_photo FROM profiles WHERE username = ?", (username,))
    result = c.fetchone()
    conn.close()
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile photo not found")
    return Response(content=result[0], media_type="image/png")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)