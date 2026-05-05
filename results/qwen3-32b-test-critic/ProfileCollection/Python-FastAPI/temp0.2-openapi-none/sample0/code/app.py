from fastapi import FastAPI, File, UploadFile, Form, Path, HTTPException, Response
import sqlite3

app = FastAPI()

def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS profiles (
            username TEXT PRIMARY KEY,
            profile_page TEXT NOT NULL,
            profile_photo BLOB NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

@app.post("/add_profile")
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...)
):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT 1 FROM profiles WHERE username = ?", (username,))
    exists = c.fetchone()
    conn.close()
    if exists:
        raise HTTPException(status_code=403, detail="Profile already exists")
    
    photo_data = await profile_photo.read()
    
    try:
        conn = sqlite3.connect('db.sqlite3')
        c = conn.cursor()
        c.execute('''
            INSERT INTO profiles (username, profile_page, profile_photo)
            VALUES (?, ?, ?)
        ''', (username, profile_page, photo_data))
        conn.commit()
        return {"status": "Profile created successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()

@app.get("/profile/{username}")
async def get_profile(username: str = Path(...)):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT profile_page FROM profiles WHERE username = ?", (username,))
    result = c.fetchone()
    conn.close()
    if not result:
        raise HTTPException(status_code=404, detail="Profile not found")
    return Response(content=result[0], media_type="text/html")

@app.get("/profile-photo/{username}")
async def get_profile_photo(username: str = Path(...)):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT profile_photo FROM profiles WHERE username = ?", (username,))
    result = c.fetchone()
    conn.close()
    if not result:
        raise HTTPException(status_code=404, detail="Profile photo not found")
    return Response(content=result[0], media_type="image/png")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)