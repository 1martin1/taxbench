import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from enum import Enum
from html import escape

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator


DB_PATH = "db.sqlite3"
MAX_LIMIT = 100
MAX_OFFSET = 10000
MAX_USERNAME_LENGTH = 100
MAX_CONTENT_LENGTH = 5000


app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)


class SortOrder(str, Enum):
    ASC = "ASC"
    DESC = "DESC"


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., examples=["hello world!"], max_length=MAX_CONTENT_LENGTH)
    username: str = Field(..., examples=["alice"], max_length=MAX_USERNAME_LENGTH)

    @field_validator("username", "content")
    @classmethod
    def validate_non_empty_trimmed(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("must be a non-empty string")
        return trimmed


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
def startup_event() -> None:
    init_db()


def render_messages_html(messages: list[sqlite3.Row], limit: int, offset: int, sort: str) -> str:
    items_html = []
    for row in messages:
        username = escape(str(row["username"]))
        content = escape(str(row["content"]))
        created_at = escape(str(row["created_at"]))
        items_html.append(
            f"""
            <article class="message">
                <header>
                    <strong>{username}</strong>
                    <small>{created_at}</small>
                </header>
                <p>{content}</p>
            </article>
            """
        )

    messages_html = "\n".join(items_html) if items_html else "<p>No messages found.</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>MyForum</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
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
            background: #fff;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 1rem;
        }}
        .message header {{
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            margin-bottom: 0.75rem;
            flex-wrap: wrap;
        }}
        .message p {{
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
        Showing messages with <code>limit={limit}</code>, <code>offset={offset}</code>, <code>sort={sort}</code>
    </div>
    <section>
        {messages_html}
    </section>
</body>
</html>"""


@app.get(
    "/messages",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {"schema": {"type": "string"}}}}},
)
def get_messages(
    limit: int = Query(
        10,
        ge=0,
        le=MAX_LIMIT,
        description="The number of messages to return",
    ),
    offset: int = Query(
        0,
        ge=0,
        le=MAX_OFFSET,
        description="The number of messages to skip before starting to collect the result set",
    ),
    sort: SortOrder = Query(
        SortOrder.DESC,
        description="The sorting direction of the messages by submission time (older or newer first)",
    ),
) -> HTMLResponse:
    sort_sql = sort.value

    with closing(get_db_connection()) as conn:
        rows = conn.execute(
            f"""
            SELECT id, username, content, created_at
            FROM messages
            ORDER BY datetime(created_at) {sort_sql}, id {sort_sql}
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

    html = render_messages_html(rows, limit=limit, offset=offset, sort=sort_sql)
    return HTMLResponse(content=html, status_code=200)


@app.post(
    "/messages",
    status_code=200,
    responses={
        200: {"description": "Successful operation"},
        400: {"description": "Invalid input"},
    },
)
def post_message(message: MessageIn) -> Response:
    username = message.username
    content = message.content

    created_at = datetime.now(timezone.utc).isoformat()

    try:
        with closing(get_db_connection()) as conn:
            conn.execute(
                """
                INSERT INTO messages (username, content, created_at)
                VALUES (?, ?, ?)
                """,
                (username, content, created_at),
            )
            conn.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="database error")

    return Response(status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)