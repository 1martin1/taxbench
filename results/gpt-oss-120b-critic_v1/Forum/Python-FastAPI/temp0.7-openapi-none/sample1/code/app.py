import os
import sqlite3
from datetime import datetime
from typing import List

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from jinja2 import Environment, select_autoescape

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
DATABASE_PATH = "db.sqlite3"

APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("Environment variable APP_SECRET must be set for security reasons.")

# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class MessageIn(BaseModel):
    username: str = Field(
        ...,
        max_length=50,
        example="alice",
        description="Username of the message author (max 50 characters).",
    )
    content: str = Field(
        ...,
        max_length=1000,
        example="hello world!",
        description="Content of the message (max 1000 characters).",
    )

# ----------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------
app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)

# ----------------------------------------------------------------------
# Database utilities
# ----------------------------------------------------------------------
def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
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
    conn.close()

@app.on_event("startup")
def on_startup() -> None:
    init_db()

# ----------------------------------------------------------------------
# Jinja2 environment with auto‑escaping
# ----------------------------------------------------------------------
jinja_env = Environment(autoescape=select_autoescape(["html", "xml"]))
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>MyForum</title>
    <style>
        body {font-family: Arial, sans-serif; margin: 2rem;}
        .message {border-bottom: 1px solid #ccc; padding: 0.5rem 0;}
        .meta {color: #555; font-size: 0.9rem;}
        .content {margin-top: 0.3rem;}
    </style>
</head>
<body>
    <h1>MyForum Messages</h1>
    {% if messages %}
        {% for msg in messages %}
            <div class="message">
                <div class="meta"><strong>{{ msg.username }}</strong> at {{ msg.created_at }}</div>
                <div class="content">{{ msg.content }}</div>
            </div>
        {% endfor %}
    {% else %}
        <p>No messages yet.</p>
    {% endif %}
</body>
</html>
"""
template = jinja_env.from_string(HTML_TEMPLATE)

# ----------------------------------------------------------------------
# Exception handling – map validation errors to 400 Bad Request
# ----------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": exc.errors()},
    )

# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.get(
    "/messages",
    response_class=HTMLResponse,
    summary="Get all previously posted messages",
)
def get_messages(
    limit: int = Query(
        10,
        ge=1,
        le=100,
        description="The number of messages to return (max 100).",
    ),
    offset: int = Query(0, ge=0, description="The number of messages to skip before starting to collect the result set"),
    sort: str = Query(
        "desc",
        regex="^(ASC|DESC|asc|desc)$",
        description="The sorting direction of the messages by submission time (older or newer first)",
    ),
):
    sort_dir = "ASC" if sort.upper() == "ASC" else "DESC"

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT username, content, created_at
        FROM messages
        ORDER BY datetime(created_at) {sort_dir}
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    rows = cur.fetchall()
    conn.close()

    messages = [
        {
            "username": row["username"],
            "content": row["content"],
            "created_at": datetime.fromisoformat(row["created_at"]).strftime("%Y-%m-%d %H:%M:%S"),
        }
        for row in rows
    ]

    html_content = template.render(messages=messages)
    return HTMLResponse(content=html_content, status_code=status.HTTP_200_OK)


@app.post(
    "/messages",
    summary="Post a new message to the forum",
    status_code=status.HTTP_200_OK,
)
def post_message(message: MessageIn):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO messages (username, content, created_at)
            VALUES (?, ?, ?)
            """,
            (message.username, message.content, datetime.utcnow().isoformat()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Database error")
    finally:
        conn.close()
    return {"detail": "Message posted successfully"}

# ----------------------------------------------------------------------
# Run the server
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)