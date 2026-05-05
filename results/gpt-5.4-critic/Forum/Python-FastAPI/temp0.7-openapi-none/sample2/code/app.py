import os
import sqlite3
import secrets
from contextlib import closing
from datetime import datetime, timezone
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.sessions import SessionMiddleware

DB_PATH = "db.sqlite3"
MAX_USERNAME_LENGTH = 100
MAX_CONTENT_LENGTH = 5000
MAX_LIMIT = 100
MAX_OFFSET = 100000


app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)


app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("APP_SECRET") or secrets.token_urlsafe(32),
)


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(default=None, example="hello world!")
    username: str | None = Field(default=None, example="alice")

    @field_validator("username")
    @classmethod
    def validate_username_length(cls, value: str | None) -> str | None:
        if value is not None and len(value) > MAX_USERNAME_LENGTH:
            raise ValueError("String too long")
        return value

    @field_validator("content")
    @classmethod
    def validate_content_length(cls, value: str | None) -> str | None:
        if value is not None and len(value) > MAX_CONTENT_LENGTH:
            raise ValueError("String too long")
        return value


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
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
        conn.commit()


def escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def render_messages_page(messages: list[sqlite3.Row], limit: int, offset: int, sort: str) -> str:
    items = []
    for row in messages:
        username = escape_html(str(row["username"]))
        content = escape_html(str(row["content"]))
        created_at = escape_html(str(row["created_at"]))
        items.append(
            f"""
            <article class="message">
                <header>
                    <strong>{username}</strong>
                    <span class="timestamp">{created_at}</span>
                </header>
                <p>{content}</p>
            </article>
            """
        )

    message_list = "\n".join(items) if items else "<p>No messages found.</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>MyForum</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 900px;
            margin: 2rem auto;
            padding: 0 1rem;
            background: #f7f7f7;
            color: #222;
        }}
        h1 {{
            margin-bottom: 0.5rem;
        }}
        .meta {{
            color: #666;
            margin-bottom: 1.5rem;
        }}
        .message {{
            background: white;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 1rem;
            box-shadow: 0 1px 2px rgba(0,0,0,0.04);
        }}
        .message header {{
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            margin-bottom: 0.75rem;
            flex-wrap: wrap;
        }}
        .timestamp {{
            color: #666;
            font-size: 0.9rem;
        }}
        p {{
            margin: 0;
            white-space: pre-wrap;
            word-break: break-word;
        }}
        code {{
            background: #eee;
            padding: 0.1rem 0.3rem;
            border-radius: 4px;
        }}
    </style>
</head>
<body>
    <h1>MyForum</h1>
    <div class="meta">
        Showing messages with <code>limit={limit}</code>, <code>offset={offset}</code>, <code>sort={escape_html(sort)}</code>
    </div>
    <section>
        {message_list}
    </section>
</body>
</html>"""


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/messages", response_class=HTMLResponse)
def get_messages(
    limit: int = Query(10, ge=0, le=MAX_LIMIT, description="The number of messages to return"),
    offset: int = Query(0, ge=0, le=MAX_OFFSET, description="The number of messages to skip before starting to collect the result set"),
    sort: Literal["ASC", "DESC"] = Query(
        "DESC",
        description="The sorting direction of the messages by submission time (older or newer first)",
    ),
) -> HTMLResponse:
    with closing(get_db_connection()) as conn:
        rows = conn.execute(
            f"""
            SELECT id, username, content, created_at
            FROM messages
            ORDER BY datetime(created_at) {sort}, id {sort}
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

    html = render_messages_page(rows, limit=limit, offset=offset, sort=sort)
    return HTMLResponse(content=html, status_code=200)


@app.post("/messages")
def post_message(message: MessageIn) -> PlainTextResponse:
    username = (message.username or "").strip()
    content = (message.content or "").strip()

    if not username or not content:
        raise HTTPException(status_code=400, detail="Invalid input")

    if len(username) > MAX_USERNAME_LENGTH or len(content) > MAX_CONTENT_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input")

    created_at = datetime.now(timezone.utc).isoformat()

    with closing(get_db_connection()) as conn:
        conn.execute(
            """
            INSERT INTO messages (username, content, created_at)
            VALUES (?, ?, ?)
            """,
            (username, content, created_at),
        )
        conn.commit()

    return PlainTextResponse("Successful operation", status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)