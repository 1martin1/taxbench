import sqlite3
from fastapi import FastAPI, Response, status, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, conint, constr
from datetime import datetime
import os
import html

app = FastAPI()

DATABASE_URL = "db.sqlite3"

def get_db():
    try:
        conn = sqlite3.connect(DATABASE_URL)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError as e:
        raise HTTPException(status_code=500, detail="Database connection failed") from e

def init_db():
    with get_db() as conn:
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        except sqlite3.Error as e:
            raise HTTPException(status_code=500, detail="Database initialization failed") from e

init_db()

class MessageCreate(BaseModel):
    content: constr(max_length=1000) = Field(..., description="Message content (max 1000 characters)")
    username: constr(max_length=100) = Field(..., description="Username (max 100 characters)")

@app.get("/messages")
def get_messages(
    limit: conint(ge=0, le=100) = 10,
    offset: conint(ge=0, le=1000) = 0,
    sort: str = "desc"
):
    try:
        sort_dir = "DESC" if sort.lower() == "desc" else "ASC"
        query = f"SELECT * FROM messages ORDER BY created_at {sort_dir} LIMIT ? OFFSET ?"
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (limit, offset))
            messages = cursor.fetchall()
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database query failed") from e
    
    html = "<html><body><h1>Messages</h1><ul>"
    for msg in messages:
        html += f"<li><strong>{html.escape(msg['username'])}</strong>: {html.escape(msg['content'])}</li>"
    html += "</ul></body></html>"
    return Response(content=html, media_type="text/html")

@app.post("/messages")
def post_message(message: MessageCreate):
    try:
        query = "INSERT INTO messages (username, content) VALUES (?, ?)"
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (message.username, message.content))
            conn.commit()
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database insert failed") from e
    return Response(status_code=200)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return Response(status_code=status.HTTP_400_BAD_REQUEST)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)