import sqlite3
from fastapi import FastAPI, File, Form, UploadFile, Response, status
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI()

@app.on_event("startup")
def create_table():
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
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...)
):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM profiles WHERE username = ?", (username,))
    exists = cursor.fetchone()
    conn.close()
    if exists:
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": "Profile already exists"})
    
    photo_data = await profile_photo.read()
    
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
            (username, profile_page, photo_data)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": "Profile already exists"})
    finally:
        conn.close()
    
    return Response(status_code=status.HTTP_201_CREATED)

@app.get("/profile/{username}")
async def get_profile(username: str):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT profile_page FROM profiles WHERE username = ?", (username,))
    result = cursor.fetchone()
    conn.close()
    if not result:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "Profile not found"})
    return HTMLResponse(content=result[0], status_code=status.HTTP_200_OK)

@app.get("/profile-photo/{username}")
async def get_profile_photo(username: str):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("SELECT profile_photo FROM profiles WHERE username = ?", (username,))
    result = cursor.fetchone()
    conn.close()
    if not result:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "Profile photo not found"})
    return Response(content=result[0], media_type="image/png")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)