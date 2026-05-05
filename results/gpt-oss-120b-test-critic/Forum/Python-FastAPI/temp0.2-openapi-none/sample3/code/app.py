import os
import sqlite3
from datetime import datetime
from typing import Generator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator
from jinja2 import Template

# ---------- Database utilities ----------
DB_PATH = "db.sqlite3"


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Dependency that provides a SQLite connection."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Create the messages table if it does not exist."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                username TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


# ---------- Pydantic models ----------
class Message(BaseModel):
    content: str = Field(
        ...,
        max_length=500,
        example="hello world!",
        description="Message text (max 500 characters)",
    )
    username: str = Field(
        ...,
        max_length=50,
        example="alice",
        description="Username of the author (max 50 characters)",
    )

    @field_validator("content", "username")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()


# ---------- FastAPI app ----------
app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)

# Initialize DB at startup
@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ---------- HTML template ----------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>MyForum</title>
    <style>
        body {font-family: Arial, sans-serif; margin: 2rem;}
        .message {border-bottom: 1px solid #ddd; padding: 0.5rem 0;}
        .meta {color: #555; font-size: 0.9rem;}
    </style>
</head>
<body>
    <h1>Forum Messages</h1>
    {% if messages %}
        {% for msg in messages %}
            <div class="message">
                <div class="content">{{ msg.content | e }}</div>
                <div class="meta">by <strong>{{ msg.username | e }}</strong> at {{ msg.created_at }}</div>
            </div>
        {% endfor %}
    {% else %}
        <p>No messages yet.</p>
    {% endif %}
</body>
</html>
"""
template = Template(HTML_TEMPLATE)


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
        description="The number of messages to return",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="The number of messages to skip before starting to collect the result set",
    ),
    sort: str = Query(
        "desc",
        regex="^(?i)(ASC|DESC)$",
        description="The sorting direction of the messages by submission time (older or newer first)",
    ),
    db: sqlite3.Connection = Depends(get_db),
):
    order = "ASC" if sort.upper() == "ASC" else "DESC"
    cursor = db.execute(
        f"""
        SELECT content, username, created_at
        FROM messages
        ORDER BY datetime(created_at) {order}
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    rows = cursor.fetchall()
    messages = [
        {
            "content": row["content"],
            "username": row["username"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    html = template.render(messages=messages)
    return HTMLResponse(content=html, status_code=200)


@app.post(
    "/messages",
    summary="Post a new message to the forum",
    status_code=200,
)
def post_message(
    message: Message,
    db: sqlite3.Connection = Depends(get_db),
):
    now = datetime.utcnow().isoformat(timespec="seconds")
    try:
        db.execute(
            """
            INSERT INTO messages (content, username, created_at)
            VALUES (?, ?, ?)
            """,
            (message.content, message.username, now),
        )
        db.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Database error")
    return JSONResponse(content={"status": "ok"}, status_code=200)


# ---------- Run server ----------
if __name__ == "__main__":
    import uvicorn

    # Optional: read secret (not used in this simple app)
    _ = os.getenv("APP_SECRET", "default_secret")
    uvicorn.run(app, host="0.0.0.0", port=5000)