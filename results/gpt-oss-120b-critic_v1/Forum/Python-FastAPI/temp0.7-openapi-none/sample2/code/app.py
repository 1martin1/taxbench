import os
import sys
import sqlite3
import html
from datetime import datetime
from enum import Enum
from typing import List

import uvicorn
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, validator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DB_PATH = "db.sqlite3"
MAX_LIMIT = 100  # Upper bound to protect against resource exhaustion
MAX_CONTENT_LENGTH = 500
MAX_USERNAME_LENGTH = 50

# ---------------------------------------------------------------------------
# Application secret handling
# ---------------------------------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    # Fail fast if the required secret is not provided
    sys.stderr.write("Error: APP_SECRET environment variable is required.\n")
    sys.exit(1)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)


# ---------------------------------------------------------------------------
# Database utilities
# ---------------------------------------------------------------------------
def get_db_connection() -> sqlite3.Connection:
    """
    Returns a new SQLite connection with thread‑safety enabled.
    """
    conn = sqlite3.connect(
        DB_PATH,
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """
    Creates the messages table if it does not already exist.
    """
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class Message(BaseModel):
    content: str = Field(..., max_length=MAX_CONTENT_LENGTH, example="hello world!")
    username: str = Field(..., max_length=MAX_USERNAME_LENGTH, example="alice")

    @validator("content", "username")
    def strip_whitespace(cls, v: str) -> str:
        # Remove leading/trailing whitespace
        return v.strip()


# ---------------------------------------------------------------------------
# Helper enums
# ---------------------------------------------------------------------------
class SortDirection(str, Enum):
    ASC = "ASC"
    DESC = "DESC"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
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
        description=f"The number of messages to return (max {MAX_LIMIT})",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="The number of messages to skip before starting to collect the result set",
    ),
    sort: SortDirection = Query(
        SortDirection.DESC,
        description="The sorting direction of the messages by submission time (older or newer first)",
    ),
):
    # Build safe ORDER BY clause using the validated enum value
    order_clause = f"ORDER BY created_at {sort.value}"

    with get_db_connection() as conn:
        cursor = conn.execute(
            f"""
            SELECT username, content, created_at
            FROM messages
            {order_clause}
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = cursor.fetchall()

    # Build simple HTML with proper escaping
    html_parts: List[str] = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        "<meta charset='utf-8'>",
        "<title>MyForum Messages</title>",
        "<style>",
        "body {font-family: Arial, sans-serif; margin: 2rem;}",
        ".msg {border-bottom: 1px solid #ddd; padding: 0.5rem 0;}",
        ".username {font-weight: bold;}",
        ".timestamp {color: #666; font-size: 0.9rem;}",
        "</style>",
        "</head>",
        "<body>",
        "<h1>Messages</h1>",
    ]

    if not rows:
        html_parts.append("<p>No messages found.</p>")
    else:
        for row in rows:
            ts: datetime = row["created_at"]
            formatted_ts = ts.strftime("%Y-%m-%d %H:%M:%S")
            escaped_username = html.escape(row["username"])
            escaped_content = html.escape(row["content"])
            html_parts.append(
                f"<div class='msg'><span class='username'>{escaped_username}</span> "
                f"<span class='timestamp'>[{formatted_ts}]</span><br>"
                f"<span class='content'>{escaped_content}</span></div>"
            )

    html_parts.extend(["</body>", "</html>"])
    html_content = "\n".join(html_parts)
    return HTMLResponse(content=html_content, status_code=status.HTTP_200_OK)


@app.post(
    "/messages",
    summary="Post a new message to the forum",
    status_code=status.HTTP_200_OK,
)
def post_message(message: Message):
    # Insert the validated message into the database
    with get_db_connection() as conn:
        try:
            conn.execute(
                """
                INSERT INTO messages (username, content)
                VALUES (?, ?)
                """,
                (message.username, message.content),
            )
            conn.commit()
        except sqlite3.IntegrityError as e:
            raise HTTPException(status_code=400, detail="Invalid input") from e

    return {"detail": "Message posted successfully"}


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)