import os
import sqlite3
import time
import html
from datetime import datetime
from threading import Lock
from typing import List

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, constr
import uvicorn

# ------------------------------
# Configuration
# ------------------------------
DB_PATH = "db.sqlite3"
MAX_LIMIT = 100
MAX_BODY_SIZE = 1_048_576  # 1 MB
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW = 60  # seconds

# ------------------------------
# FastAPI app
# ------------------------------
app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)

# ------------------------------
# Database setup
# ------------------------------
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row


def init_db() -> None:
    """Create the messages table if it doesn't exist."""
    with conn:
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


@app.on_event("shutdown")
def on_shutdown() -> None:
    conn.close()


# ------------------------------
# Pydantic models
# ------------------------------
class Message(BaseModel):
    username: constr(min_length=1, max_length=30) = Field(..., example="alice")
    content: constr(min_length=1, max_length=500) = Field(..., example="hello world!")


# ------------------------------
# Helper functions
# ------------------------------
def insert_message(message: Message) -> None:
    now_iso = datetime.utcnow().isoformat()
    with conn:
        conn.execute(
            "INSERT INTO messages (username, content, created_at) VALUES (?, ?, ?)",
            (message.username, message.content, now_iso),
        )


def fetch_messages(limit: int, offset: int, sort: str) -> List[sqlite3.Row]:
    order = "ASC" if sort == "ASC" else "DESC"
    cur = conn.execute(
        f"""
        SELECT username, content, created_at
        FROM messages
        ORDER BY datetime(created_at) {order}
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    return cur.fetchall()


# ------------------------------
# Middleware
# ------------------------------
class BodySizeLimitMiddleware:
    def __init__(self, app: FastAPI, max_body_size: int):
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # Look for Content-Length header
            for header_name, header_value in scope["headers"]:
                if header_name == b"content-length":
                    try:
                        cl = int(header_value)
                        if cl > self.max_body_size:
                            response = JSONResponse(
                                {"detail": "Request body too large"},
                                status_code=413,
                            )
                            await response(scope, receive, send)
                            return
                    except ValueError:
                        pass  # let FastAPI handle malformed header later
        await self.app(scope, receive, send)


class RateLimitMiddleware:
    def __init__(self, app: FastAPI, max_requests: int, window_seconds: int):
        self.app = app
        self.max_requests = max_requests
        self.window = window_seconds
        self.ip_path_times = {}
        self.lock = Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        ip = client[0] if client else "unknown"
        path = scope["path"]
        key = (ip, path)
        now = time.time()

        with self.lock:
            timestamps = self.ip_path_times.get(key, [])
            # Remove timestamps outside the window
            timestamps = [ts for ts in timestamps if now - ts < self.window]

            if len(timestamps) >= self.max_requests:
                response = JSONResponse(
                    {"detail": "Too many requests"},
                    status_code=429,
                )
                await response(scope, receive, send)
                return

            timestamps.append(now)
            self.ip_path_times[key] = timestamps

        await self.app(scope, receive, send)


app.add_middleware(BodySizeLimitMiddleware, max_body_size=MAX_BODY_SIZE)
app.add_middleware(RateLimitMiddleware, max_requests=RATE_LIMIT_REQUESTS, window_seconds=RATE_LIMIT_WINDOW)


# ------------------------------
# Exception handlers
# ------------------------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Convert FastAPI's 422 validation errors to 400 as per OpenAPI spec
    return JSONResponse(
        status_code=400,
        content={"detail": exc.errors()},
    )


# ------------------------------
# Endpoints
# ------------------------------
@app.get(
    "/messages",
    response_class=HTMLResponse,
    summary="Get all previously posted messages",
)
def get_messages(
    limit: int = Query(
        10,
        ge=1,
        le=MAX_LIMIT,
        description="The number of messages to return",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="The number of messages to skip before starting to collect the result set",
    ),
    sort: str = Query(
        "desc",
        description="The sorting direction of the messages by submission time (older or newer first)",
    ),
):
    sort_upper = sort.upper()
    if sort_upper not in ("ASC", "DESC"):
        raise HTTPException(status_code=400, detail="Invalid sort parameter")

    try:
        rows = fetch_messages(limit=limit, offset=offset, sort=sort_upper)
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Build simple HTML with escaped content
    html_parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        "<meta charset='utf-8'>",
        "<title>MyForum Messages</title>",
        "<style>",
        "body {font-family: Arial, sans-serif; margin: 2rem;}",
        ".message {border-bottom: 1px solid #ccc; padding: 0.5rem 0;}",
        ".meta {color: #555; font-size: 0.9rem;}",
        "</style>",
        "</head>",
        "<body>",
        "<h1>Messages</h1>",
        "<div>",
    ]

    if not rows:
        html_parts.append("<p>No messages found.</p>")
    else:
        for row in rows:
            username = html.escape(row["username"])
            content = html.escape(row["content"]).replace("\n", "<br>")
            created_at = html.escape(row["created_at"])
            html_parts.append(
                f"<div class='message'><div class='meta'><strong>{username}</strong> @ {created_at}</div>"
                f"<div class='content'>{content}</div></div>"
            )

    html_parts.extend(["</div>", "</body>", "</html>"])
    return HTMLResponse("\n".join(html_parts))


@app.post(
    "/messages",
    summary="Post a new message to the forum",
)
def post_message(message: Message):
    try:
        insert_message(message)
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    return JSONResponse(content={"status": "ok"})


# ------------------------------
# Run server
# ------------------------------
if __name__ == "__main__":
    # The APP_SECRET environment variable could be used for future extensions (e.g., JWT)
    _ = os.getenv("APP_SECRET", "")
    uvicorn.run(app, host="0.0.0.0", port=5000)