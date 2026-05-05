import os
import sqlite3
import html
from datetime import datetime
from typing import List

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.cors import CORSMiddleware

# ---------- Database Setup ----------
DB_PATH = "db.sqlite3"
# Use check_same_thread=False to allow usage across async contexts
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row


def init_db() -> None:
    """Create the messages table if it does not exist."""
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


# ---------- Pydantic Models ----------
class MessageIn(BaseModel):
    username: str = Field(..., example="alice", max_length=30, min_length=1)
    content: str = Field(..., example="hello world!", max_length=500, min_length=1)

    @field_validator("username", "content")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty or whitespace")
        return v


# ---------- FastAPI App ----------
app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)

# Allow CORS for testing (optional)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


# ---------- Helper Functions ----------
def insert_message(username: str, content: str) -> int:
    """Insert a new message and return its id."""
    created_at = datetime.utcnow().isoformat()
    with conn:
        cur = conn.execute(
            "INSERT INTO messages (username, content, created_at) VALUES (?, ?, ?)",
            (username, content, created_at),
        )
        return cur.lastrowid


def fetch_messages(limit: int, offset: int, sort: str) -> List[sqlite3.Row]:
    """Retrieve messages according to pagination and sorting."""
    order = "ASC" if sort.upper() == "ASC" else "DESC"
    cur = conn.execute(
        f"""
        SELECT id, username, content, created_at
        FROM messages
        ORDER BY datetime(created_at) {order}
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    return cur.fetchall()


# ---------- Routes ----------
@app.get(
    "/messages",
    response_class=HTMLResponse,
    summary="Get all previously posted messages",
)
async def get_messages(
    limit: int = Query(10, ge=1, description="The number of messages to return"),
    offset: int = Query(0, ge=0, description="The number of messages to skip before starting to collect the result set"),
    sort: str = Query(
        "desc",
        regex="^(ASC|DESC|asc|desc)$",
        description="The sorting direction of the messages by submission time (older or newer first)",
    ),
):
    rows = fetch_messages(limit=limit, offset=offset, sort=sort)
    # Simple HTML rendering with proper escaping
    html_parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        "<meta charset='utf-8'>",
        "<title>MyForum - Messages</title>",
        "<style>",
        "body {font-family: Arial, sans-serif; margin: 2rem;}",
        ".message {border-bottom: 1px solid #ddd; padding: 0.5rem 0;}",
        ".meta {color: #555; font-size: 0.9rem;}",
        "form {margin-top: 2rem;}",
        "input, textarea {width: 100%; padding: 0.5rem; margin-top: 0.5rem;}",
        "button {margin-top: 0.5rem; padding: 0.5rem 1rem;}",
        "</style>",
        "</head>",
        "<body>",
        "<h1>MyForum Messages</h1>",
    ]

    if rows:
        for row in rows:
            escaped_username = html.escape(row["username"])
            escaped_content = html.escape(row["content"]).replace("\n", "<br>")
            html_parts.append(
                f"<div class='message'>"
                f"<div class='meta'><strong>{escaped_username}</strong> at {row['created_at']}</div>"
                f"<div class='content'>{escaped_content}</div>"
                f"</div>"
            )
    else:
        html_parts.append("<p>No messages found.</p>")

    # Simple form to post a new message
    html_parts.extend(
        [
            "<h2>Post a new message</h2>",
            "<form method='post' action='/messages' enctype='application/json' onsubmit='submitForm(event)'>",
            "<label>Username:</label>",
            "<input type='text' name='username' required>",
            "<label>Content:</label>",
            "<textarea name='content' rows='3' required></textarea>",
            "<button type='submit'>Send</button>",
            "</form>",
            "<script>",
            "async function submitForm(e) {",
            "  e.preventDefault();",
            "  const form = e.target;",
            "  const data = {",
            "    username: form.username.value,",
            "    content: form.content.value",
            "  };",
            "  const resp = await fetch('/messages', {",
            "    method: 'POST',",
            "    headers: {'Content-Type': 'application/json'},",
            "    body: JSON.stringify(data)",
            "  });",
            "  if (resp.ok) { location.reload(); } else { alert('Failed to post message'); }",
            "}",
            "</script>",
            "</body>",
            "</html>",
        ]
    )
    return HTMLResponse("\n".join(html_parts))


@app.post(
    "/messages",
    summary="Post a new message to the forum",
    status_code=status.HTTP_200_OK,
)
async def post_message(message: MessageIn):
    try:
        insert_message(username=message.username, content=message.content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(content={"detail": "Message posted successfully"})


# ---------- Main ----------
if __name__ == "__main__":
    import uvicorn

    # Optional: read secret (not used in this simple app)
    APP_SECRET = os.getenv("APP_SECRET", "default_secret")
    uvicorn.run(app, host="0.0.0.0", port=5000)