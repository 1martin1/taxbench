import html
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Literal

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field


DB_PATH = "db.sqlite3"

app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, example="hello world!")
    username: str = Field(..., min_length=1, example="alice")


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
    items = []
    for row in messages:
        username = html.escape(row["username"])
        content = html.escape(row["content"]).replace("\n", "<br>")
        created_at = html.escape(row["created_at"])
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

    messages_html = "\n".join(items) if items else "<p>No messages yet.</p>"

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
        .meta {{
            margin-bottom: 20px;
            color: #666;
        }}
        .message {{
            background: white;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 12px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.04);
        }}
        .message header {{
            display: flex;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 8px;
            flex-wrap: wrap;
        }}
        .timestamp {{
            color: #666;
            font-size: 0.9rem;
        }}
        p {{
            margin: 0;
            white-space: normal;
            word-break: break-word;
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
        <div class="meta">
            Showing up to <code>{limit}</code> messages, starting at offset <code>{offset}</code>, sorted <code>{html.escape(sort)}</code>.
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
    normalized_sort = sort.upper()
    if normalized_sort not in {"ASC", "DESC"}:
        raise HTTPException(status_code=400, detail="Invalid sort value")

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

    content = render_messages_page(rows, limit, offset, normalized_sort)
    return HTMLResponse(content=content, status_code=200)


@app.post("/messages")
def post_message(message: Message = Body(...)) -> JSONResponse:
    username = message.username.strip()
    content = message.content.strip()

    if not username or not content:
        raise HTTPException(status_code=400, detail="username and content must not be empty")

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


@app.get("/", response_class=HTMLResponse)
def root(request: Request) -> HTMLResponse:
    return HTMLResponse(
        content="""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>MyForum</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; }
        code { background: #f0f0f0; padding: 2px 6px; border-radius: 4px; }
        pre { background: #f8f8f8; padding: 12px; border-radius: 8px; overflow-x: auto; }
    </style>
</head>
<body>
    <h1>MyForum</h1>
    <p>This service exposes the forum API.</p>
    <ul>
        <li><code>GET /messages</code> - view messages as HTML</li>
        <li><code>POST /messages</code> - post a message as JSON</li>
    </ul>
    <pre>{
  "username": "alice",
  "content": "hello world!"
}</pre>
</body>
</html>
""",
        status_code=200,
    )


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)