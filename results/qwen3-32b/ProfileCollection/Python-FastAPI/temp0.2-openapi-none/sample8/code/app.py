from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Response
import sqlite3

app = FastAPI()

DB_NAME = 'db.sqlite3'

def get_db():
    db = sqlite3.connect(DB_NAME)
    db.execute('''CREATE TABLE IF NOT EXISTS profiles
                 (username TEXT PRIMARY KEY, profile_page TEXT, profile_photo BLOB)''')
    return db

@app.post("/add_profile", status_code=201)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...)
):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT username FROM profiles WHERE username = ?", (username,))
    if cursor.fetchone():
        db.close()
        raise HTTPException(status_code=403, detail="Profile already exists")
    photo_data = await profile_photo.read()
    cursor.execute(
        "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
        (username, profile_page, photo_data)
    )
    db.commit()
    db.close()
    return {"message": "Profile created successfully"}

@app.get("/profile/{username}")
def get_profile(username: str):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT profile_page FROM profiles WHERE username = ?", (username,))
    result = cursor.fetchone()
    db.close()
    if not result:
        raise HTTPException(status_code=404, detail="Profile not found")
    return Response(content=result[0], media_type="text/html")

@app.get("/profile-photo/{username}")
def get_profile_photo(username: str):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT profile_photo FROM profiles WHERE username = ?", (username,))
    result = cursor.fetchone()
    db.close()
    if not result:
        raise HTTPException(status_code=404, detail="Profile photo not found")
    return Response(content=result[0], media_type="image/png")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)