from fastapi import FastAPI, HTTPException, status, Request
from fastapi.responses import HTMLResponse
from fastapi.exceptions import RequestValidationError
import sqlite3
from pydantic import BaseModel, StringConstraints
from typing import Annotated
import html

app = FastAPI()

def init_db():
    db_path = 'db.sqlite3'
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

class MessageCreate(BaseModel):
    username: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    content: Annotated[str, StringConstraints(min_length=1, max_length=1000)]

@app.get("/messages")
def get_messages(limit: int = 10, offset: int = 0, sort: str = "desc"):
    sort = sort.upper()
    if sort not in ['ASC', 'DESC']:
        sort = 'DESC'
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute(f"SELECT username, content FROM messages ORDER BY timestamp {sort} LIMIT ? OFFSET ?", (limit, offset))
    rows = c.fetchall()
    conn.close()
    
    html_content = "<html><body><h1>Messages</h1>"
    for username, content in rows:
        html_content += f"<div><strong>{html.escape(username)}</strong><br>{html.escape(content)}</div><hr>"
    html_content += "</body></html>"
    
    return HTMLResponse(content=html_content)

@app.post("/messages")
def post_message(message: MessageCreate):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO messages (username, content) VALUES (?, ?)", 
                 (message.username, message.content))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()
    return {"status": "success"}

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    raise HTTPException(status_code=400, detail="Invalid input")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)