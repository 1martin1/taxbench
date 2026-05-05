from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
import sqlite3
from html import escape as html_escape
from datetime import datetime

app = FastAPI()

class MessageCreate(BaseModel):
    content: str = Field(..., max_length=1000)
    username: str = Field(..., max_length=50)

def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT NOT NULL,
                  content TEXT NOT NULL,
                  created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/messages")
def get_messages(limit: int = 10, offset: int = 0, sort: str = "desc"):
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="Limit must be between 1 and 100")
    if offset < 0:
        raise HTTPException(status_code=400, detail="Offset must be a non-negative integer")
    
    sort = sort.upper()
    if sort not in ["ASC", "DESC"]:
        raise HTTPException(status_code=400, detail="Invalid sort direction")
    
    order_clause = "ASC" if sort == "ASC" else "DESC"
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute(f"SELECT id, username, content, created_at FROM messages ORDER BY created_at {order_clause} LIMIT ? OFFSET ?", 
              (limit, offset))
    rows = c.fetchall()
    conn.close()
    
    html = "<!DOCTYPE html><html><head><title>Messages</title></head><body><h1>Messages</h1><ul>"
    for row in rows:
        msg_id, username, content, created_at = row
        html += f"<li><strong>{html_escape(username)}</strong>: {html_escape(content)} <em>({created_at})</em></li>"
    html += "</ul></body></html>"
    return Response(content=html, media_type="text/html")

@app.post("/messages")
def post_message(message: MessageCreate):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("INSERT INTO messages (username, content) VALUES (?, ?)", 
              (message.username, message.content))
    conn.commit()
    conn.close()
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)