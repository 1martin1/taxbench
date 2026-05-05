import html
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Literal

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
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

    @field_validator("content", "username")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
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
def startup() -> None:
    init_db()


def render_messages_html(messages: list[sqlite3.Row], limit: int, offset: int, sort: str) -> str:
    items = []
    for message in messages:
        username = html.escape(str(message["username"]))
        content = html.escape(str(message["content"])).replace("\n", "<br>")
        created_at = html.escape(str(message["created_at"]))
        items.append(
            f"""
            <article class="message">
                <div class="meta">
                    <strong>{username}</strong>
                    <span>{created_at}</span>
                </div>
                <p>{content}</p>
            </article>
            """
        )

    messages_html = "\n".join(items) if items else "<p>No messages found.</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>MyForum</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 2rem;
            background: #f5f5f5;
            color: #222;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
        }}
        h1 {{
            margin-top: 0;
        }}
        .info {{
            margin-bottom: 1rem;
            color: #555;
        }}
        .message {{
            background: white;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 1rem;
            box-shadow: 0 1px 2px rgba(0,0,0,0.04);
        }}
        .meta {{
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            margin-bottom: 0.5rem;
            color: #666;
            font-size: 0.95rem;
        }}
        p {{
            margin: 0;
            white-space: normal;
            word-break: break-word;
        }}
        code {{
            background: #eee;
            padding: 0.15rem 0.35rem;
            border-radius: 4px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>MyForum</h1>
        <div class="info">
            Showing messages with <code>limit={limit}</code>, <code>offset={offset}</code>, <code>sort={html.escape(sort)}</code>
        </div>
        {messages_html}
    </div>
</body>
</html>"""


@app.get("/messages", response_class=HTMLResponse)
def get_messages(
    limit: int = Query(10, ge=0, description="The number of messages to return"),
    offset: int = Query(0, ge=0, description="The number of messages to skip before starting to collect the result set"),
    sort: Literal["ASC", "DESC", "asc", "desc"] = Query(
        "desc",
        description="The sorting direction of the messages by submission time (older or newer first)",
    ),
) -> HTMLResponse:
    normalized_sort = str(sort).upper()
    query = f"""
        SELECT id, username, content, created_at
        FROM messages
        ORDER BY datetime(created_at) {normalized_sort}, id {normalized_sort}
        LIMIT ? OFFSET ?
    """

    with closing(get_db_connection()) as conn:
        rows = conn.execute(query, (limit, offset)).fetchall()

    content = render_messages_html(rows, limit=limit, offset=offset, sort=normalized_sort)
    return HTMLResponse(content=content, status_code=200)


@app.post("/messages")
def post_message(message: MessageIn = Body(...)) -> Response:
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        with closing(get_db_connection()) as conn:
            conn.execute(
                "INSERT INTO messages (username, content, created_at) VALUES (?, ?, ?)",
                (message.username, message.content, created_at),
            )
            conn.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Invalid input")

    return Response(status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)