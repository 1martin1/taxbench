import os
import sqlite3
from datetime import datetime
from enum import Enum
from typing import List

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, validator
from jinja2 import Environment, select_autoescape

# ---------- Pydantic models ----------
class MessageIn(BaseModel):
    content: str = Field(..., example="hello world!")
    username: str = Field(..., example="alice")

    @validator("content")
    def content_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content must not be empty")
        return v

    @validator("username")
    def username_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("username must not be empty")
        return v


# ---------- FastAPI app ----------
app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)

DB_PATH = "db.sqlite3"

# ---------- Database utilities ----------
def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db_connection() as conn:
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


@app.on_event("startup")
def startup_event() -> None:
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

jinja_env = Environment(autoescape=select_autoescape(["html", "xml"]))


def render_messages_html(messages: List[sqlite3.Row]) -> str:
    formatted = []
    for row in messages:
        formatted.append(
            {
                "username": row["username"],
                "content": row["content"],
                "created_at": row["created_at"],  # already stored as readable string
            }
        )
    template = jinja_env.from_string(HTML_TEMPLATE)
    return template.render(messages=formatted)


# ---------- Enums ----------
class SortDirection(str, Enum):
    ASC = "ASC"
    DESC = "DESC"


# ---------- Exception handlers ----------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Convert FastAPI's 422 responses to 400 as per OpenAPI spec
    return JSONResponse(
        status_code=400,
        content={"detail": exc.errors()},
    )


# ---------- Endpoints ----------
@app.get(
    "/messages",
    response_class=HTMLResponse,
    summary="Get all previously posted messages",
)
def get_messages(
    limit: int = Query(10, ge=1, description="The number of messages to return"),
    offset: int = Query(0, ge=0, description="The number of messages to skip before starting to collect the result set"),
    sort: SortDirection = Query(SortDirection.DESC, description="The sorting direction of the messages by submission time (older or newer first)"),
):
    order = sort.value
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


@app.post(
    "/messages",
    summary="Post a new message to the forum",
)
def post_message(message: MessageIn):
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        try:
            conn.execute(
                """
                INSERT INTO messages (content, username, created_at)
                VALUES (?, ?, ?)
                """,
                (message.content, message.username, now_str),
            )
            conn.commit()
        except sqlite3.Error:
            raise HTTPException(status_code=500, detail="Database error")
    return JSONResponse(content={"detail": "Message posted successfully"})


# ---------- Run server ----------
if __name__ == "__main__":
    import uvicorn

    # Load optional secret (not used in this simple app but kept for completeness)
    APP_SECRET = os.getenv("APP_SECRET", "default-secret")

    uvicorn.run(app, host="0.0.0.0", port=5000)