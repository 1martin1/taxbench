import sqlite3
from fastapi import FastAPI, Response, HTTPException
from pydantic import BaseModel, Field
from html import escape
from datetime import datetime

app = FastAPI()

@app.on_event("startup")
def create_table():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            submission_time TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

class Message(BaseModel):
    content: str = Field(..., max_length=1000)
    username: str = Field(..., max_length=100)

@app.get("/messages")
async def get_messages(limit: int = 10, offset: int = 0, sort: str = "DESC"):
    sort_upper = sort.upper()
    if sort_upper not in ["ASC", "DESC"]:
        raise HTTPException(status_code=400, detail="Invalid sort direction")
    
    if limit <= 0:
        raise HTTPException(status_code=400, detail="Limit must be positive")
    if limit > 100:
        raise HTTPException(status_code=400, detail="Limit exceeds maximum allowed value of 100")
    if offset < 0:
        raise HTTPException(status_code=400, detail="Offset cannot be negative")
    
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    query = f"""
        SELECT username, content, submission_time
        FROM messages
        ORDER BY submission_time {sort_upper}
        LIMIT ? OFFSET ?
    """
    cursor.execute(query, (limit, offset))
    rows = cursor.fetchall()
    conn.close()
    
    html = "<html><body><h1>Messages</h1><ul>"
    for row in rows:
        username, content, submission_time = row
        html += f"<li><strong>{escape(username)}</strong>: {escape(content)} <em>({submission_time})</em></li>"
    html += "</ul></body></html>"
    
    return Response(content=html, media_type="text/html")

@app.post("/messages")
async def post_message(message: Message):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    submission_time = datetime.now().isoformat()
    cursor.execute(
        "INSERT INTO messages (username, content, submission_time) VALUES (?, ?, ?)",
        (message.username, message.content, submission_time)
    )
    conn.commit()
    conn.close()
    return Response(status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)