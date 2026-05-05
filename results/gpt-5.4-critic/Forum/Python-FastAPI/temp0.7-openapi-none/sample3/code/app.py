import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Literal

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

DB_PATH = "db.sqlite3"
MAX_LIMIT = 100
MAX_CONTENT_LENGTH = 5000
MAX_USERNAME_LENGTH = 100
MAX_REQUEST_BODY_BYTES = 16 * 1024
MAX_MESSAGES_TOTAL = 10000
POSTS_PER_WINDOW = 30
RATE_WINDOW_SECONDS = 60

app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)

_rate_lock = threading.Lock()
_rate_state: dict[str, list[float]] = {}


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=MAX_CONTENT_LENGTH)
    username: str = Field(..., min_length=1, max_length=MAX_USERNAME_LENGTH)

    @field_validator("content", "username")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at)"
        )


def count_messages() -> int:
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM messages").fetchone()
        return int(row["count"])


def insert_message(username: str, content: str) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM messages").fetchone()
        if int(row["count"]) >= MAX_MESSAGES_TOTAL:
            raise ValueError("forum storage limit reached")
        conn.execute(
            """
            INSERT INTO messages (username, content, created_at)
            VALUES (?, ?, ?)
            """,
            (username, content, created_at),
        )


def list_messages(limit: int, offset: int, sort: str):
    order = "ASC" if sort == "ASC" else "DESC"
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT id, username, content, created_at
            FROM messages
            ORDER BY datetime(created_at) {order}, id {order}
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    return rows


def html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def render_messages_page(messages, limit: int, offset: int, sort: str) -> str:
    items = []
    for row in messages:
        username = html_escape(str(row["username"]))
        content = html_escape(str(row["content"])).replace("\n", "<br>")
        created_at = html_escape(str(row["created_at"]))
        items.append(
            f"""
            <article class="message">
                <header>
                    <strong>{username}</strong>
                    <span class="timestamp">{created_at}</span>
                </header>
                <div class="content">{content}</div>
            </article>
            """
        )

    if not items:
        items_html = '<p class="empty">No messages yet.</p>'
    else:
        items_html = "\n".join(items)

    next_offset = offset + limit
    prev_offset = max(offset - limit, 0)
    opposite_sort = "ASC" if sort == "DESC" else "DESC"

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
            margin: 0 auto;
            padding: 2rem 1rem;
            background: #f7f7f7;
            color: #222;
        }}
        h1 {{
            margin-top: 0;
        }}
        .controls {{
            margin-bottom: 1.5rem;
            padding: 1rem;
            background: white;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}
        .controls a {{
            margin-right: 1rem;
            color: #0b57d0;
            text-decoration: none;
        }}
        .message {{
            background: white;
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 1rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
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
        .content {{
            white-space: normal;
            word-break: break-word;
        }}
        .empty {{
            background: white;
            border-radius: 8px;
            padding: 1rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        }}
    </style>
</head>
<body>
    <h1>MyForum</h1>
    <div class="controls">
        <div><strong>Viewing messages</strong></div>
        <div>limit={limit} | offset={offset} | sort={sort}</div>
        <div style="margin-top: 0.75rem;">
            <a href="/messages?limit={limit}&offset={prev_offset}&sort={sort}">Previous</a>
            <a href="/messages?limit={limit}&offset={next_offset}&sort={sort}">Next</a>
            <a href="/messages?limit={limit}&offset=0&sort={opposite_sort}">Toggle sort</a>
        </div>
    </div>
    {items_html}
</body>
</html>
"""


def get_client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def check_rate_limit(client_ip: str) -> bool:
    now = time.time()
    with _rate_lock:
        timestamps = _rate_state.get(client_ip, [])
        timestamps = [ts for ts in timestamps if now - ts < RATE_WINDOW_SECONDS]
        if len(timestamps) >= POSTS_PER_WINDOW:
            _rate_state[client_ip] = timestamps
            return False
        timestamps.append(now)
        _rate_state[client_ip] = timestamps
        return True


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path == "/messages" and request.method == "POST":
        return JSONResponse(status_code=400, content={"detail": "Invalid input"})
    raise exc


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                return Response(status_code=413)
        except ValueError:
            return Response(status_code=400)
    return await call_next(request)


@app.on_event("startup")
async def startup_event() -> None:
    await run_in_threadpool(init_db)


@app.get(
    "/messages",
    response_class=HTMLResponse,
    responses={200: {"content": {"text/html": {}}}},
)
async def get_messages(
    limit: int = Query(
        10, ge=1, le=MAX_LIMIT, description="The number of messages to return"
    ),
    offset: int = Query(
        0,
        ge=0,
        description="The number of messages to skip before starting to collect the result set",
    ),
    sort: Literal["ASC", "DESC"] = Query(
        "DESC",
        description="The sorting direction of the messages by submission time (older or newer first)",
    ),
):
    rows = await run_in_threadpool(list_messages, limit, offset, sort)
    html = render_messages_page(rows, limit, offset, sort)
    return HTMLResponse(content=html, status_code=200)


@app.post(
    "/messages",
    responses={
        200: {"description": "Successful operation"},
        400: {"description": "Invalid input"},
    },
)
async def post_message(request: Request, payload: MessageIn = Body(...)):
    if not check_rate_limit(get_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too Many Requests")
    try:
        await run_in_threadpool(insert_message, payload.username, payload.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid input") from exc
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail="Internal Server Error") from exc
    return Response(status_code=200)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)