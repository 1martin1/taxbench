import os
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware


DB_PATH = "db.sqlite3"
MAX_LIMIT = 100
MAX_REQUEST_BODY_BYTES = 16 * 1024
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_GET_MAX = 120
RATE_LIMIT_POST_MAX = 30


app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., examples=["hello world!"])
    username: str = Field(..., examples=["alice"])

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("content must be a string")
        value = value.strip()
        if not value:
            raise ValueError("content must not be empty")
        if len(value) > 5000:
            raise ValueError("content is too long")
        return value

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("username must be a string")
        value = value.strip()
        if not value:
            raise ValueError("username must not be empty")
        if len(value) > 255:
            raise ValueError("username is too long")
        return value


class RateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def allow(self, key: str, scope: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        bucket = self._buckets[(scope, key)]
        cutoff = now - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


rate_limiter = RateLimiter()


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        client_host = request.client.host if request.client else "unknown"
        path = request.url.path
        method = request.method.upper()

        if path == "/messages":
            if method == "POST":
                content_length = request.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > MAX_REQUEST_BODY_BYTES:
                            return PlainTextResponse("Request body too large", status_code=413)
                    except ValueError:
                        return PlainTextResponse("Invalid Content-Length header", status_code=400)

                if not rate_limiter.allow(
                    key=client_host,
                    scope="POST:/messages",
                    limit=RATE_LIMIT_POST_MAX,
                    window_seconds=RATE_LIMIT_WINDOW_SECONDS,
                ):
                    return PlainTextResponse("Too Many Requests", status_code=429)

            elif method == "GET":
                if not rate_limiter.allow(
                    key=client_host,
                    scope="GET:/messages",
                    limit=RATE_LIMIT_GET_MAX,
                    window_seconds=RATE_LIMIT_WINDOW_SECONDS,
                ):
                    return PlainTextResponse("Too Many Requests", status_code=429)

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response


app.add_middleware(SecurityMiddleware)


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
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


def html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def normalize_sort(sort: str) -> str:
    sort_upper = sort.upper()
    if sort_upper in {"ASC", "DESC"}:
        return sort_upper
    raise HTTPException(status_code=400, detail="Invalid sort value")


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    if request.method.upper() == "POST" and request.url.path == "/messages":
        return JSONResponse(status_code=400, content={"detail": exc.errors()})
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/messages", response_class=HTMLResponse, summary="Get all previously posted messages")
def get_messages(
    limit: int = Query(
        default=10,
        ge=0,
        le=MAX_LIMIT,
        description="The number of messages to return",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="The number of messages to skip before starting to collect the result set",
    ),
    sort: str = Query(
        default="desc",
        description="The sorting direction of the messages by submission time (older or newer first)",
        openapi_extra={"enum": ["ASC", "DESC"]},
    ),
) -> HTMLResponse:
    safe_sort = normalize_sort(sort)

    try:
        with closing(get_db_connection()) as conn:
            rows = conn.execute(
                f"""
                SELECT id, username, content, created_at
                FROM messages
                ORDER BY datetime(created_at) {safe_sort}, id {safe_sort}
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Database error")

    items = []
    for row in rows:
        username = html_escape(str(row["username"]))
        content = html_escape(str(row["content"])).replace("\n", "<br>")
        created_at = html_escape(str(row["created_at"]))
        items.append(
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

    messages_html = "\n".join(items) if items else "<p>No messages found.</p>"

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>MyForum</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 2rem auto;
            max-width: 800px;
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
            align-items: baseline;
        }}
        .message p {{
            margin: 0;
            white-space: normal;
            overflow-wrap: break-word;
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
        Showing up to <code>{limit}</code> messages, offset <code>{offset}</code>, sorted <code>{safe_sort}</code>.
    </div>
    <section>
        {messages_html}
    </section>
</body>
</html>"""
    return HTMLResponse(content=page, status_code=200)


@app.post(
    "/messages",
    summary="Post a new message to the forum",
    responses={
        200: {"description": "Successful operation"},
        400: {"description": "Invalid input"},
    },
)
def post_message(message: MessageIn = Body(...)) -> PlainTextResponse:
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    try:
        with closing(get_db_connection()) as conn:
            conn.execute(
                """
                INSERT INTO messages (username, content, created_at)
                VALUES (?, ?, ?)
                """,
                (message.username, message.content, created_at),
            )
            conn.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Database error")

    return PlainTextResponse("Successful operation", status_code=200)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request) -> HTMLResponse:
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>MyForum</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 2rem auto;
            max-width: 800px;
            padding: 0 1rem;
            line-height: 1.5;
        }
        pre {
            background: #f4f4f4;
            padding: 1rem;
            border-radius: 8px;
            overflow-x: auto;
        }
        code {
            font-family: monospace;
        }
        a {
            color: #0b57d0;
        }
    </style>
</head>
<body>
    <h1>MyForum</h1>
    <p>Welcome to MyForum.</p>
    <p>Use <a href="/messages">/messages</a> to view messages.</p>
    <p>POST JSON to <code>/messages</code> with this shape:</p>
    <pre><code>{
  "content": "hello world!",
  "username": "alice"
}</code></pre>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)