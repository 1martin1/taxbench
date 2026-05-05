from fastapi import FastAPI, Depends, Header, HTTPException, File, UploadFile, Form, Response, HTMLResponse
import sqlite3
import os
import bleach

app = FastAPI()

def get_api_key(x_api_key: str = Header(...)):
    app_secret = os.getenv("APP_SECRET")
    if not app_secret or x_api_key != app_secret:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return x_api_key

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS profiles (
            username TEXT PRIMARY KEY,
            profile_page TEXT NOT NULL,
            profile_photo BLOB NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

@app.post("/add_profile")
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(..., max_length=100000),
    profile_photo: UploadFile = File(..., max_size=1024*1024*5),
    api_key: str = Depends(get_api_key)
):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM profiles WHERE username = ?", (username,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=403, detail="Profile already exists, creation forbidden")
    
    # Sanitize HTML to prevent XSS
    allowed_tags = ['p', 'b', 'i', 'u', 'em', 'strong', 'a', 'img', 'br', 'div', 'span', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'table', 'tr', 'td', 'th', 'pre', 'code', 'blockquote']
    allowed_attrs = {
        'a': ['href', 'title'],
        'img': ['src', 'alt', 'title']
    }
    cleaned_profile_page = bleach.clean(profile_page, tags=allowed_tags, attributes=allowed_attrs, strip=True)
    
    # Validate PNG file signature
    file = profile_photo.file
    original_pos = file.tell()
    signature = file.read(8)
    file.seek(original_pos)
    
    if len(signature) < 8 or signature != b'\x89PNG\r\n\x1a\n':
        raise HTTPException(status_code=400, detail="Invalid PNG file")
    
    photo_data = await profile_photo.read()
    
    cursor.execute(
        "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
        (username, cleaned_profile_page, photo_data)
    )
    conn.commit()
    conn.close()
    return Response(status_code=201)

@app.get("/profile/{username}")
async def get_profile(username: str):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT profile_page FROM profiles WHERE username = ?", (username,))
    result = cursor.fetchone()
    conn.close()
    if not result:
        raise HTTPException(status_code=404, detail="Profile not found")
    return HTMLResponse(content=result[0], status_code=200)

@app.get("/profile-photo/{username}")
async def get_profile_photo(username: str):
    conn = sqlite3.connect('db.sqlite3')
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