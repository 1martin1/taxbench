from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Response
import sqlite3
import uvicorn
import re

app = FastAPI()

MAX_HTML_SIZE = 1024 * 1024  # 1MB
MAX_PHOTO_SIZE = 5 * 1024 * 1024  # 5MB

@app.on_event("startup")
def create_tables():
    try:
        conn = sqlite3.connect('db.sqlite3')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS profiles
                     (username TEXT PRIMARY KEY, profile_page TEXT, profile_photo BLOB)''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error initializing database: {e}")
        raise

@app.post("/add_profile")
async def add_profile(
    username: str = Form(...),
    profile_page: UploadFile = File(...),
    profile_photo: UploadFile = File(...)
):
    if len(username) > 32:  # Prevent overly long usernames
        raise HTTPException(status_code=400, detail="Username too long")
    
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT * FROM profiles WHERE username = ?", (username,))
    if c.fetchone():
        conn.close()
        raise HTTPException(status_code=403, detail="Profile already exists")
    
    try:
        html_content = (await profile_page.read()).decode('utf-8')
        # Sanitize HTML to prevent XSS (basic script tag removal)
        html_content = re.sub(r'<script.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        
        if len(html_content) > MAX_HTML_SIZE:
            conn.close()
            raise HTTPException(status_code=400, detail="HTML content too large")
            
        photo_content = await profile_photo.read()
        if len(photo_content) > MAX_PHOTO_SIZE:
            conn.close()
            raise HTTPException(status_code=400, detail="Profile photo too large")
            
        c.execute("INSERT INTO profiles VALUES (?, ?, ?)",
                  (username, html_content, photo_content))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid input or database error") from e
    finally:
        conn.close()
    
    return Response(status_code=201)

@app.get("/profile/{username}")
async def get_profile(username: str):
    if len(username) > 32:
        raise HTTPException(status_code=404, detail="Profile not found")
        
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT profile_page FROM profiles WHERE username = ?", (username,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    return Response(content=result[0], media_type="text/html")

@app.get("/profile-photo/{username}")
async def get_profile_photo(username: str):
    if len(username) > 32:
        raise HTTPException(status_code=404, detail="Profile photo not found")
        
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT profile_photo FROM profiles WHERE username = ?", (username,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        raise HTTPException(status_code=404, detail="Profile photo not found")
    
    return Response(content=result[0], media_type="image/png")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)