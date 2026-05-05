from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
import sqlite3
from pydantic import BaseModel
from typing import Optional
import os

app = FastAPI()

class Message(BaseModel):
    username: str
    content: str

def get_db():
    db = sqlite3.connect('db.sqlite3')
    db.row_factory = sqlite3.Row
    return db

@app.on_event("startup")
def create_tables():
    db = get_db()
    db.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            submission_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.commit()
    db.close()

@app.get("/messages")
async def get_messages(limit: int = 10, offset: int = 0, sort: str = 'DESC'):
    sort_dir = sort.upper()
    if sort_dir not in ['ASC', 'DESC']:
        raise HTTPException(status_code=400, detail="Invalid sort direction")
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute(f'''
        SELECT * FROM messages
        ORDER BY submission_time {sort_dir}
        LIMIT ? OFFSET ?
    ''', (limit, offset))
    messages = cursor.fetchall()
    db.close()
    
    html_content = "<html><head><title>MyForum Messages</title></head><body>"
    html_content += "<h1>Messages</h1><ul>"
    for msg in messages:
        html_content += f"<li><strong>{msg['username']}</strong>: {msg['content']} <em>({msg['submission_time']})</em></li>"
    html_content += "</ul></body></html>"
    
    return HTMLResponse(content=html_content)

@app.post("/messages")
async def post_message(message: Message):
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        INSERT INTO messages (username, content)
        VALUES (?, ?)
    ''', (message.username, message.content))
    db.commit()
    db.close()
    return Response(status_code=200)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return Response(status_code=400, content="Invalid input")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)