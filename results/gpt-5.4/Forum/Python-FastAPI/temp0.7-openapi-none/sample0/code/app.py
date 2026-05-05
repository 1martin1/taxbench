import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from html import escape
from typing import Literal, Optional

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

DB_PATH = "db.sqlite3"

app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., example="hello world!")
    username: str = Field(..., example="alice")

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("content must be a string")
        value = value.strip()
        if not value:
            raise ValueError("content must not be empty")
        return value

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("username must be a string")
        value = value.strip()
        if not value:
            raise ValueError("username must not be empty")
        if len(value) > 100:
            raise ValueError("username must be at most 100 characters")
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


@app.on_event("startup")
def on_startup() -> None:
    init_db()


def render_messages_page(messages: list[sqlite3.Row], limit: int, offset: int, sort: str) -> str:
    rows_html = []
    for message in messages:
        username = escape(str(message["username"]))
        content = escape(str(message["content"])).replace("\n", "<br>")
        created_at = escape(str(message["created_at"]))
        rows_html.append(
            f"""
            <article class="message">
                <div class="meta">
                    <strong>{username}</strong>
                    <span>{created_at}</span>
                </div>
                <div class="content">{content}</div>
            </article>
            """
        )

    messages_html = "\n".join(rows_html) if rows_html else '<p class="empty">No messages found.</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>MyForum</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root {{
            color-scheme: light dark;
        }}
        body {{
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 0;
            background: #f4f4f4;
            color: #222;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
            padding: 24px;
        }}
        h1 {{
            margin-top: 0;
        }}
        .toolbar {{
            background: #fff;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 20px;
        }}
        .message {{
            background: #fff;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 12px;
        }}
        .meta {{
            display: flex;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 10px;
            color: #555;
            font-size: 14px;
        }}
        .content {{
            white-space: normal;
            word-break: break-word;
            line-height: 1.5;
        }}
        .empty {{
            background: #fff;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 16px;
        }}
        code {{
            background: #eee;
            padding: 2px 6px;
            border-radius: 4px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>MyForum</h1>
        <div class="toolbar">
            <p>Showing messages with <code>limit={limit}</code>, <code>offset={offset}</code>, <code>sort={escape(sort)}</code>.</p>
            <p>Post messages with a JSON <code>POST /messages</code> request containing <code>{{"username": "...", "content": "..."}}</code>.</p>
        </div>
        {messages_html}
    </div>
</body>
</html>"""


@app.get("/messages", response_class=HTMLResponse, responses={200: {"content": {"text/html": {}}}})
def get_messages(
    limit: int = Query(10, ge=0, description="The number of messages to return"),
    offset: int = Query(0, ge=0, description="The number of messages to skip before starting to collect the result set"),
    sort: Optional[str] = Query("desc", description="The sorting direction of the messages by submission time (older or newer first)"),
) -> HTMLResponse:
    sort_value = (sort or "desc").upper()
    if sort_value not in {"ASC", "DESC"}:
        raise HTTPException(status_code=400, detail="sort must be ASC or DESC")

    with closing(get_db_connection()) as conn:
        cursor = conn.execute(
            f"""
            SELECT id, username, content, created_at
            FROM messages
            ORDER BY datetime(created_at) {sort_value}, id {sort_value}
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        messages = cursor.fetchall()

    html = render_messages_page(messages, limit=limit, offset=offset, sort=sort_value)
    return HTMLResponse(content=html, status_code=200)


@app.post("/messages", responses={200: {"description": "Successful operation"}, 400: {"description": "Invalid input"}})
def post_message(message: MessageIn = Body(...)) -> JSONResponse:
    username = message.username.strip()
    content = message.content.strip()

    if not username or not content:
        raise HTTPException(status_code=400, detail="username and content are required")

    created_at = datetime.now(timezone.utc).isoformat()

    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO messages (username, content, created_at)
                VALUES (?, ?, ?)
                """,
                (username, content, created_at),
            )
            conn.commit()
            message_id = cursor.lastrowid
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Invalid input")

    return JSONResponse(
        status_code=200,
        content={
            "id": message_id,
            "username": username,
            "content": content,
            "created_at": created_at,
        },
    )


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)