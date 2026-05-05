from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from enum import Enum
import sqlite3
from starlette.responses import HTMLResponse
import os
import html
from contextlib import asynccontextmanager

class MessageCreate(BaseModel):
    username: str
    content: str

class SortEnum(str, Enum):
    ASC = "ASC"
    DESC = "DESC"

@app.post("/messages")
async def post_message(message: MessageCreate):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO messages (username, content) VALUES (?, ?)", 
                      (message.username, message.content))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()
    return {"status": "success"}

@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = sqlite3.connect("db.sqlite3")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            submission_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.close()
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/messages")
async def get_messages(limit: int = 10, offset: int = 0, sort: SortEnum = SortEnum.DESC):
    order_by = f"submission_time {sort.value}"
    query = f"SELECT * FROM messages ORDER BY {order_by} LIMIT ? OFFSET ?"
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    try:
        cursor.execute(query, (limit, offset))
        messages = cursor.fetchall()
    finally:
        conn.close()

    html_content = """
    <html>
        <head><title>Messages</title></head>
        <body>
            <h1>Messages</h1>
            <ul>
    """
    for msg in messages:
        html_content += f"""
            <li>
                <strong>{html.escape(msg[1])}</strong>: {html.escape(msg[2])} <em>({msg[3]})</em>
            </li>
        """
    html_content += """
            </ul>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)