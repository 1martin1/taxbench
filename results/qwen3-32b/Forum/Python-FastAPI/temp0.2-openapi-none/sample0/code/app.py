import sqlite3
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI()

DATABASE = 'db.sqlite3'

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

@app.on_event("startup")
def create_tables():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

class MessageCreate(BaseModel):
    username: str
    content: str

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return Response(status_code=400)

@app.get("/messages")
async def get_messages(limit: int = 10, offset: int = 0, sort: str = "DESC"):
    if sort not in ["ASC", "DESC"]:
        sort = "DESC"
    with get_db() as conn:
        query = f"SELECT * FROM messages ORDER BY created_at {sort} LIMIT ? OFFSET ?"
        cursor = conn.execute(query, (limit, offset))
        messages = cursor.fetchall()
    html_content = "<html><body><h1>Messages</h1><ul>"
    for msg in messages:
        html_content += f"<li><strong>{msg['username']}</strong> ({msg['created_at']}): {msg['content']}</li>"
    html_content += "</ul></body></html>"
    return HTMLResponse(content=html_content, status_code=200)

@app.post("/messages")
async def post_message(message: MessageCreate):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO messages (username, content) VALUES (?, ?)",
                       (message.username, message.content))
        conn.commit()
    return Response(status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)