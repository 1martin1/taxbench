import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_500_INTERNAL_SERVER_ERROR

DB_PATH = "db.sqlite3"
MAX_LIMIT = 100
MAX_USERNAME_LENGTH = 100
MAX_CONTENT_LENGTH = 5000

app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: Optional[str] = Field(default=None, max_length=MAX_CONTENT_LENGTH, examples=["hello world!"])
    username: Optional[str] = Field(default=None, max_length=MAX_USERNAME_LENGTH, examples=["alice"])


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
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
        conn.commit()


def escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return Response(status_code=HTTP_400_BAD_REQUEST)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get(
    "/messages",
    response_class=HTMLResponse,
    summary="Get all previously posted messages",
)
def get_messages(
    limit: int = Query(10, ge=0, le=MAX_LIMIT, description="The number of messages to return"),
    offset: int = Query(
        0,
        ge=0,
        description="The number of messages to skip before starting to collect the result set",
    ),
    sort: str = Query(
        "DESC",
        pattern="^(ASC|DESC)$",
        description="The sorting direction of the messages by submission time (older or newer first)",
    ),
):
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT id, username, content, created_at
            FROM messages
            ORDER BY datetime(created_at) {sort}, id {sort}
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

    items_html = []
    for row in rows:
        username = escape_html(row["username"])
        content = escape_html(row["content"]).replace("\n", "<br>")
        created_at = escape_html(row["created_at"])
        items_html.append(
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

    messages_html = "\n".join(items_html) if items_html else "<p>No messages yet.</p>"

    html = f"""<!DOCTYPE html>
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
        .controls {{
            margin-bottom: 1.5rem;
            color: #555;
        }}
        .message {{
            background: #fff;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 1rem;
            box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
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
            white-space: normal;
            word-break: break-word;
        }}
        .api-note {{
            margin-top: 2rem;
            padding: 1rem;
            background: #eef6ff;
            border: 1px solid #cfe3ff;
            border-radius: 8px;
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
    <div class="controls">
        Showing messages with <code>limit={limit}</code>,
        <code>offset={offset}</code>,
        <code>sort={sort}</code>
    </div>
    <section>
        {messages_html}
    </section>
    <div class="api-note">
        Post new messages with a JSON request to <code>POST /messages</code>.
    </div>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)


@app.post(
    "/messages",
    summary="Post a new message to the forum",
    responses={
        200: {"description": "Successful operation"},
        400: {"description": "Invalid input"},
    },
)
def post_message(message: MessageIn = Body(...)):
    username = (message.username or "").strip()
    content = (message.content or "").strip()

    if not username or not content:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="Invalid input")

    created_at = datetime.now(timezone.utc).isoformat()

    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO messages (username, content, created_at)
                VALUES (?, ?, ?)
                """,
                (username, content, created_at),
            )
            conn.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")

    return Response(status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)