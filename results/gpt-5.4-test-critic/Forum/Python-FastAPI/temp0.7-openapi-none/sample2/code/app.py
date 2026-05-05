import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape
from typing import Literal

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, field_validator

DB_PATH = "db.sqlite3"

app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)


class Message(BaseModel):
    content: str = Field(..., example="hello world!")
    username: str = Field(..., example="alice")

    @field_validator("content", "username")
    @classmethod
    def validate_not_empty(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
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


@app.on_event("startup")
def on_startup() -> None:
    init_db()


def render_messages_page(messages: list[sqlite3.Row], limit: int, offset: int, sort: str) -> str:
    items = []
    for message in messages:
        username = escape(message["username"])
        content = escape(message["content"]).replace("\n", "<br>")
        created_at = escape(message["created_at"])
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

    message_list_html = "\n".join(items) if items else "<p>No messages yet.</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>MyForum</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 0;
            background: #f5f5f5;
            color: #222;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
            padding: 2rem 1rem;
        }}
        h1 {{
            margin-top: 0;
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
            align-items: baseline;
            flex-wrap: wrap;
        }}
        .timestamp {{
            color: #777;
            font-size: 0.9rem;
        }}
        p {{
            margin: 0;
            white-space: normal;
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
    <main class="container">
        <h1>MyForum</h1>
        <div class="meta">
            Showing messages with <code>limit={limit}</code>,
            <code>offset={offset}</code>,
            <code>sort={escape(sort)}</code>
        </div>
        {message_list_html}
    </main>
</body>
</html>"""


@app.get("/messages", response_class=HTMLResponse, responses={200: {"content": {"text/html": {}}}})
def get_messages(
    limit: int = Query(10, ge=0, description="The number of messages to return"),
    offset: int = Query(0, ge=0, description="The number of messages to skip before starting to collect the result set"),
    sort: Literal["ASC", "DESC", "asc", "desc"] = Query(
        "desc",
        description="The sorting direction of the messages by submission time (older or newer first)",
    ),
) -> HTMLResponse:
    normalized_sort = sort.upper()

    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT id, username, content, created_at
            FROM messages
            ORDER BY datetime(created_at) {normalized_sort}, id {normalized_sort}
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

    html = render_messages_page(rows, limit=limit, offset=offset, sort=normalized_sort)
    return HTMLResponse(content=html, status_code=200)


@app.post("/messages", status_code=200, responses={200: {"description": "Successful operation"}, 400: {"description": "Invalid input"}})
def post_message(message: Message = Body(...)) -> Response:
    try:
        username = message.username.strip()
        content = message.content.strip()
        created_at = datetime.now(timezone.utc).isoformat()

        with get_db() as conn:
            conn.execute(
                "INSERT INTO messages (username, content, created_at) VALUES (?, ?, ?)",
                (username, content, created_at),
            )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid input") from exc

    return Response(status_code=200)


@app.get("/", response_class=HTMLResponse)
def root(request: Request) -> HTMLResponse:
    base = str(request.base_url).rstrip("/")
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>MyForum</title>
</head>
<body>
    <h1>MyForum</h1>
    <p>This forum exposes its main interface at <a href="{base}/messages">{base}/messages</a>.</p>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)