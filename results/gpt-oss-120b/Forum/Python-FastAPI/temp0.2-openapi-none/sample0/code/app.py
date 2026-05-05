import os
import sqlite3
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, validator
from jinja2 import Template

# ---------- Pydantic models ----------
class MessageIn(BaseModel):
    content: str = Field(..., example="hello world!")
    username: str = Field(..., example="alice")

    @validator('content')
    def content_not_empty(cls, v):
        if not v.strip():
            raise ValueError('content must not be empty')
        return v

    @validator('username')
    def username_not_empty(cls, v):
        if not v.strip():
            raise ValueError('username must not be empty')
        return v

# ---------- FastAPI app ----------
app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)

DB_PATH = "db.sqlite3"

# ---------- Database utilities ----------
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                username TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        conn.commit()

@app.on_event("startup")
def startup_event():
    init_db()

# ---------- HTML template ----------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>MyForum</title>
    <style>
        body {font-family: Arial, sans-serif; margin: 2rem;}
        .message {border-bottom: 1px solid #ddd; padding: 0.5rem 0;}
        .meta {color: #555; font-size: 0.9rem;}
    </style>
</head>
<body>
    <h1>Messages</h1>
    {% if messages %}
        {% for msg in messages %}
            <div class="message">
                <div class="meta"><strong>{{ msg.username }}</strong> at {{ msg.created_at }}</div>
                <div class="content">{{ msg.content }}</div>
            </div>
        {% endfor %}
    {% else %}
        <p>No messages yet.</p>
    {% endif %}
</body>
</html>
"""

def render_messages_html(messages: List[sqlite3.Row]) -> str:
    # Convert rows to dicts with formatted datetime
    formatted = []
    for row in messages:
        formatted.append({
            "username": row["username"],
            "content": row["content"],
            "created_at": datetime.fromisoformat(row["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
        })
    template = Template(HTML_TEMPLATE)
    return template.render(messages=formatted)

# ---------- Endpoints ----------
@app.get("/messages", response_class=HTMLResponse, summary="Get all previously posted messages")
def get_messages(
    limit: int = Query(10, ge=1, description="The number of messages to return"),
    offset: int = Query(0, ge=0, description="The number of messages to skip before starting to collect the result set"),
    sort: str = Query("DESC", regex="^(ASC|DESC)$", description="The sorting direction of the messages by submission time (older or newer first)"),
):
    order = "ASC" if sort.upper() == "ASC" else "DESC"
    query = f"""
        SELECT username, content, created_at
        FROM messages
        ORDER BY created_at {order}
        LIMIT ? OFFSET ?
    """
    with get_db_connection() as conn:
        rows = conn.execute(query, (limit, offset)).fetchall()
    html = render_messages_html(rows)
    return HTMLResponse(content=html, media_type="text/html")

@app.post("/messages", summary="Post a new message to the forum")
def post_message(message: MessageIn):
    now = datetime.utcnow().isoformat(timespec='seconds')
    with get_db_connection() as conn:
        try:
            conn.execute(
                """
                INSERT INTO messages (content, username, created_at)
                VALUES (?, ?, ?)
                """,
                (message.content, message.username, now)
            )
            conn.commit()
        except sqlite3.Error as e:
            raise HTTPException(status_code=500, detail="Database error")
    return JSONResponse(content={"detail": "Message posted successfully"})

# ---------- Run server ----------
if __name__ == "__main__":
    import uvicorn

    # Load optional secret (not used in this simple app but kept for completeness)
    APP_SECRET = os.getenv("APP_SECRET", "default-secret")

    uvicorn.run(app, host="0.0.0.0", port=5000)