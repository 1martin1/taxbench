import html
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Literal

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError


DB_PATH = "db.sqlite3"


app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., example="hello world!")
    username: str = Field(..., example="alice")


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
    for row in messages:
        username = html.escape(str(row["username"]))
        content = html.escape(str(row["content"])).replace("\n", "<br>")
        created_at = html.escape(str(row["created_at"]))
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

    messages_html = "\n".join(items) if items else '<p class="empty">No messages found.</p>'

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
            padding: 2rem;
            background: #f5f5f5;
            color: #222;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            background: white;
            padding: 2rem;
            border-radius: 12px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.08);
        }}
        h1 {{
            margin-top: 0;
        }}
        .meta {{
            color: #666;
            margin-bottom: 1.5rem;
        }}
        .message {{
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 1rem;
            background: #fafafa;
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
        .empty {{
            color: #666;
            font-style: italic;
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
        <p class="meta">
            Showing messages with <code>limit={limit}</code>,
            <code>offset={offset}</code>,
            <code>sort={html.escape(sort)}</code>
        </p>
        {messages_html}
    </div>
</body>
</html>"""


@app.get(
    "/messages",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {"schema": {"type": "string"}}}}},
)
def get_messages(
    limit: int = Query(10, ge=0, description="The number of messages to return"),
    offset: int = Query(0, ge=0, description="The number of messages to skip before starting to collect the result set"),
    sort: Literal["ASC", "DESC", "asc", "desc"] = Query(
        "desc",
        description="The sorting direction of the messages by submission time (older or newer first)",
    ),
) -> HTMLResponse:
    normalized_sort = sort.upper()

    with closing(get_db_connection()) as conn:
        rows = conn.execute(
            f"""
            SELECT id, username, content, created_at
            FROM messages
            ORDER BY datetime(created_at) {normalized_sort}, id {normalized_sort}
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

    html_content = render_messages_html(rows, limit=limit, offset=offset, sort=normalized_sort)
    return HTMLResponse(content=html_content, status_code=200)


@app.post("/messages", responses={200: {"description": "Successful operation"}, 400: {"description": "Invalid input"}})
async def post_message(request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    try:
        message = Message.model_validate(payload)
    except ValidationError:
        raise HTTPException(status_code=400, detail="Invalid input")

    username = message.username.strip()
    content = message.content.strip()

    if not username or not content:
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

    return JSONResponse(status_code=200, content={"status": "ok"})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)