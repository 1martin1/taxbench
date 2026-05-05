import sqlite3
import html
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.responses import HTMLResponse
from typing import Optional

app = FastAPI()

def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  content TEXT NOT NULL,
                  username TEXT NOT NULL,
                  submission_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

class Message(BaseModel):
    content: str
    username: str

@app.post("/messages")
async def post_message(message: Message):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO messages (content, username) VALUES (?, ?)",
                  (message.content, message.username))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()
    return {"status": "success"}

@app.get("/messages")
async def get_messages(limit: int = 10, offset: int = 0, sort: str = "desc"):
    if limit < 0 or offset < 0:
        raise HTTPException(status_code=400, detail="Limit and offset must be non-negative")
    sort_upper = sort.upper()
    if sort_upper not in ["ASC", "DESC"]:
        raise HTTPException(status_code=400, detail="Invalid sort direction")
    
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    try:
        query = f"SELECT * FROM messages ORDER BY submission_time {sort_upper} LIMIT ? OFFSET ?"
        c.execute(query, (limit, offset))
        rows = c.fetchall()
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Database error")
    finally:
        conn.close()
    
    html_content = "<html><head><title>Messages</title></head><body>"
    html_content += "<h1>Messages</h1><ul>"
    for row in rows:
        username = html.escape(row['username'])
        content = html.escape(row['content'])
        html_content += f"<li><strong>{username}</strong>: {content}</li>"
    html_content += "</ul></body></html>"
    
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)