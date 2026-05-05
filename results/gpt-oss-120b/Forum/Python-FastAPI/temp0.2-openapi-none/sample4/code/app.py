import os
import sqlite3
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError
from jinja2 import Template

# ---------- Database Setup ----------
DB_PATH = "db.sqlite3"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

# ---------- Pydantic Models ----------
class Message(BaseModel):
    username: str = Field(..., example="alice")
    content: str = Field(..., example="hello world!")

# ---------- FastAPI App ----------
app = FastAPI(title="MyForum", description="MyForum is a simple webapp that allows users to post and read messages.", version="1.0.11")

@app.on_event("startup")
def on_startup():
    init_db()

# ---------- HTML Template ----------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>MyForum</title>
    <style>
        body {font-family: Arial, sans-serif; margin: 2rem;}
        .message {border-bottom: 1px solid #ddd; padding: 0.5rem 0;}
        .meta {color: #555; font-size: 0.9rem;}
        .content {margin-top: 0.3rem;}
    </style>
</head>
<body>
    <h1>Messages</h1>
    {% if messages %}
        {% for msg in messages %}
            <div class="message">
                <div class="meta"><strong>{{ msg.username }}</strong> @ {{ msg.created_at }}</div>
                <div class="content">{{ msg.content }}</div>
            </div>
        {% endfor %}
    {% else %}
        <p>No messages yet.</p>
    {% endif %}
</body>
</html>
"""

template = Template(HTML_TEMPLATE)

# ---------- Routes ----------
@app.get("/messages", response_class=HTMLResponse, summary="Get all previously posted messages")
def get_messages(
    limit: int = Query(10, ge=1, description="The number of messages to return"),
    offset: int = Query(0, ge=0, description="The number of messages to skip before starting to collect the result set"),
    sort: str = Query("desc", regex="^(ASC|DESC|asc|desc)$", description="The sorting direction of the messages by submission time (older or newer first)"),
):
    sort_dir = sort.upper()
    conn = get_db_connection()
    cursor = conn.cursor()
    query = f"""
        SELECT username, content, created_at
        FROM messages
        ORDER BY datetime(created_at) {sort_dir}
        LIMIT ? OFFSET ?
    """
    cursor.execute(query, (limit, offset))
    rows = cursor.fetchall()
    conn.close()

    messages = [
        {
            "username": row["username"],
            "content": row["content"],
            "created_at": datetime.fromisoformat(row["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
        }
        for row in rows
    ]

    rendered_html = template.render(messages=messages)
    return HTMLResponse(content=rendered_html, status_code=200)

@app.post("/messages", summary="Post a new message to the forum")
def post_message(message: Message):
    # Basic validation already handled by Pydantic
    conn = get_db_connection()
    cursor = conn.cursor()
    now_iso = datetime.utcnow().isoformat()
    try:
        cursor.execute(
            """
            INSERT INTO messages (username, content, created_at)
            VALUES (?, ?, ?)
            """,
            (message.username, message.content, now_iso)
        )
        conn.commit()
    except sqlite3.Error as e:
        conn.close()
        raise HTTPException(status_code=500, detail="Database error")
    conn.close()
    return JSONResponse(content={"status": "ok"}, status_code=200)

# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, log_level="info")