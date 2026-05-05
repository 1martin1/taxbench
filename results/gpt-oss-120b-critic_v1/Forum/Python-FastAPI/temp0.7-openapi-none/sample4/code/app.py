import os
import enum
import sqlite3
from datetime import datetime
from typing import List

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, constr
from jinja2 import Environment, select_autoescape

# ---------- Configuration ----------
DB_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "default_secret")  # Loaded as per spec, not used directly

# ---------- Database Setup ----------
def get_db_connection() -> sqlite3.Connection:
    # check_same_thread=False allows usage across FastAPI's async threads
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
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

# ---------- Models ----------
class SortDirection(str, enum.Enum):
    asc = "asc"
    desc = "desc"

class MessageIn(BaseModel):
    username: constr(strip_whitespace=True, min_length=1, max_length=50) = Field(..., example="alice")
    content: constr(strip_whitespace=True, min_length=1, max_length=500) = Field(..., example="hello world!")

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

jinja_env = Environment(autoescape=select_autoescape(["html", "xml"]))
template = jinja_env.from_string(HTML_TEMPLATE)

# ---------- FastAPI App ----------
app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)

@app.on_event("startup")
def on_startup():
    init_db()

# ---------- Endpoints ----------
@app.get(
    "/messages",
    response_class=HTMLResponse,
    summary="Get all previously posted messages",
)
def get_messages(
    limit: int = Query(
        10,
        ge=1,
        le=100,
        description="The number of messages to return (max 100)",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="The number of messages to skip before starting to collect the result set",
    ),
    sort: SortDirection = Query(
        SortDirection.desc,
        description="The sorting direction of the messages by submission time (older or newer first)",
    ),
):
    conn = get_db_connection()
    cursor = conn.cursor()
    order = "ASC" if sort == SortDirection.asc else "DESC"
    try:
        cursor.execute(
            f"""
            SELECT username, content, created_at
            FROM messages
            ORDER BY created_at {order}
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    messages = [
        {
            "username": row["username"],
            "content": row["content"],
            "created_at": datetime.fromisoformat(row["created_at"]).strftime("%Y-%m-%d %H:%M:%S"),
        }
        for row in rows
    ]

    rendered_html = template.render(messages=messages)
    return HTMLResponse(content=rendered_html, status_code=200)


@app.post(
    "/messages",
    status_code=status.HTTP_200_OK,
    summary="Post a new message to the forum",
)
def post_message(message: MessageIn):
    conn = get_db_connection()
    cursor = conn.cursor()
    now_iso = datetime.utcnow().replace(microsecond=0).isoformat()
    try:
        cursor.execute(
            """
            INSERT INTO messages (username, content, created_at)
            VALUES (?, ?, ?)
            """,
            (message.username, message.content, now_iso),
        )
        conn.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Database error")
    finally:
        conn.close()
    return {"detail": "Message posted successfully"}

# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)