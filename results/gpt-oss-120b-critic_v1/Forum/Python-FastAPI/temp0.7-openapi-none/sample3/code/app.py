import os
import sqlite3
import html
from datetime import datetime
from enum import Enum
from typing import List, Optional

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    Depends,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError, validator

# Configuration constants
DB_PATH = "db.sqlite3"
MAX_LIMIT = 100               # maximum number of messages that can be requested at once
MAX_CONTENT_LENGTH = 10 * 1024  # 10 KiB maximum request body size for POST /messages
MAX_USERNAME_LENGTH = 30
MAX_MESSAGE_LENGTH = 500

app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)


def get_db() -> sqlite3.Connection:
    """Provide a thread‑safe SQLite connection."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the messages table if it does not exist."""
    conn = get_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


class SortOrder(str, Enum):
    ASC = "ASC"
    DESC = "DESC"


class MessageIn(BaseModel):
    username: str = Field(..., example="alice", max_length=MAX_USERNAME_LENGTH)
    content: str = Field(..., example="hello world!", max_length=MAX_MESSAGE_LENGTH)

    @validator("username", "content")
    def strip_whitespace(cls, v: str) -> str:
        return v.strip()


def fetch_messages(
    limit: int, offset: int, sort: SortOrder, db: sqlite3.Connection
) -> List[sqlite3.Row]:
    order = "DESC" if sort == SortOrder.DESC else "ASC"
    try:
        cursor = db.cursor()
        cursor.execute(
            f"""
            SELECT username, content, created_at
            FROM messages
            ORDER BY datetime(created_at) {order}
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = cursor.fetchall()
        return rows
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Database error")


@app.middleware("http")
async def enforce_content_length(request: Request, call_next):
    # Enforce a maximum request body size for POST /messages
    if request.method == "POST" and request.url.path == "/messages":
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_CONTENT_LENGTH:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={"detail": "Request body too large"},
                    )
            except ValueError:
                pass  # fall back to reading the body
    response = await call_next(request)
    return response


@app.get(
    "/messages",
    response_class=HTMLResponse,
    summary="Get all previously posted messages",
)
def get_messages(
    limit: int = Query(
        10,
        ge=1,
        le=MAX_LIMIT,
        description=f"The number of messages to return (max {MAX_LIMIT})",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="The number of messages to skip before starting to collect the result set",
    ),
    sort: SortOrder = Query(
        SortOrder.DESC,
        description="The sorting direction of the messages by submission time (older or newer first)",
    ),
):
    db = get_db()
    try:
        rows = fetch_messages(limit, offset, sort, db)
    finally:
        db.close()

    html_items = []
    for row in rows:
        try:
            ts = datetime.fromisoformat(row["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            ts = row["created_at"]
        escaped_username = html.escape(row["username"])
        escaped_content = html.escape(row["content"])
        html_items.append(
            f"<li><strong>{escaped_username}</strong>: {escaped_content} <em>({ts})</em></li>"
        )

    if html_items:
        html_body = "<ul>\n" + "\n".join(html_items) + "\n</ul>"
    else:
        html_body = "<p>No messages.</p>"

    html_page = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>MyForum - Messages</title>
        <meta charset="utf-8"/>
    </head>
    <body>
        <h1>Messages</h1>
        {html_body}
    </body>
    </html>
    """
    return HTMLResponse(content=html_page, status_code=200)


@app.post(
    "/messages",
    summary="Post a new message to the forum",
    response_model=dict,
    responses={
        200: {"description": "Successful operation"},
        400: {"description": "Invalid input"},
        413: {"description": "Request body too large"},
    },
)
def post_message(message: MessageIn):
    db = get_db()
    try:
        cursor = db.cursor()
        now = datetime.utcnow().isoformat(timespec="seconds")
        cursor.execute(
            """
            INSERT INTO messages (username, content, created_at)
            VALUES (?, ?, ?)
            """,
            (message.username, message.content, now),
        )
        db.commit()
        message_id = cursor.lastrowid
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        db.close()
    return JSONResponse(content={"id": message_id, "status": "created"}, status_code=200)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000, log_level="info")