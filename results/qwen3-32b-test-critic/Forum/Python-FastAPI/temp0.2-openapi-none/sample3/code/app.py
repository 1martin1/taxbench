from fastapi import FastAPI, Query, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from enum import Enum
import sqlite3
import os

app = FastAPI()

# Database setup
def get_db():
    conn = sqlite3.connect('db.sqlite3', check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()

@app.on_event("startup")
def startup():
    conn = sqlite3.connect('db.sqlite3', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

class SortDirection(str, Enum):
    asc = "ASC"
    desc = "DESC"

class MessageIn(BaseModel):
    username: str
    content: str

@app.get("/messages", response_class=HTMLResponse)
async def get_messages(
    limit: int = Query(default=10, ge=0),
    offset: int = Query(default=0, ge=0),
    sort: SortDirection = Query(default=SortDirection.desc),
    db: sqlite3.Connection = Depends(get_db)
):
    cursor = db.cursor()
    sort_order = 'DESC' if sort == SortDirection.desc else 'ASC'
    query = f"SELECT username, content FROM messages ORDER BY timestamp {sort_order} LIMIT ? OFFSET ?"
    cursor.execute(query, (limit, offset))
    messages = cursor.fetchall()
    html = "<html><body><h1>Messages</h1><ul>"
    for username, content in messages:
        html += f"<li><strong>{username}</strong>: {content}</li>"
    html += "</ul></body></html>"
    return html

@app.post("/messages")
async def post_message(message: MessageIn, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("INSERT INTO messages (username, content) VALUES (?, ?)", (message.username, message.content))
    db.commit()
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)