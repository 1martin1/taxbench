import os
import sqlite3
import html
from datetime import datetime
from typing import List

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

# ---------- Configuration ----------
DATABASE_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "default_secret")  # Loaded as required but not used

# ---------- Database Utilities ----------
def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
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

# ---------- Pydantic Models ----------
class MessageIn(BaseModel):
    username: str = Field(..., example="alice")
    content: str = Field(..., example="hello world!")

    @field_validator("username", "content")
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

# ---------- FastAPI App ----------
app = FastAPI(
    title="MyForum",
    description="MyForum is a simple webapp that allows users to post and read messages.",
    version="1.0.11",
)

# Ensure DB is ready at startup
@app.on_event("startup")
def on_startup():
    init_db()

# ---------- Middleware ----------
class DBSessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.db = get_db_connection()
        response = await call_next(request)
        request.state.db.close()
        return response

app.add_middleware(DBSessionMiddleware)

# ---------- Exception Handlers ----------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Map validation errors to 400 Bad Request as per OpenAPI spec
    return JSONResponse(status_code=400, content={"detail": "Invalid input"})

# ---------- Routes ----------
@app.get(
    "/messages",
    response_class=HTMLResponse,
    summary="Get all previously posted messages",
)
def get_messages(
    request: Request,
    limit: int = Query(10, ge=1, description="The number of messages to return"),
    offset: int = Query(0, ge=0, description="The number of messages to skip before starting to collect the result set"),
    sort: str = Query(
        "desc",
        regex="^(?i)(ASC|DESC)$",
        description="The sorting direction of the messages by submission time (older or newer first)",
    ),
):
    """
    Returns an HTML page with a list of messages.
    """
    db = request.state.db
    cursor = db.cursor()
    order_clause = "ASC" if sort.upper() == "ASC" else "DESC"
    query = f"""
        SELECT username, content, created_at
        FROM messages
        ORDER BY datetime(created_at) {order_clause}
        LIMIT ? OFFSET ?
    """
    cursor.execute(query, (limit, offset))
    rows = cursor.fetchall()

    # Simple HTML rendering with proper escaping
    html_parts: List[str] = [
        "<!DOCTYPE html>",
        "<html lang='en'>",
        "<head>",
        "<meta charset='UTF-8'>",
        "<title>MyForum - Messages</title>",
        "<style>",
        "body {font-family: Arial, sans-serif; margin: 2rem;}",
        ".message {border-bottom: 1px solid #ddd; padding: 0.5rem 0;}",
        ".meta {color: #555; font-size: 0.9rem;}",
        "</style>",
        "</head>",
        "<body>",
        "<h1>Messages</h1>",
    ]

    if rows:
        for row in rows:
            created = datetime.fromisoformat(row["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
            escaped_username = html.escape(row["username"])
            escaped_content = html.escape(row["content"])
            html_parts.append(
                f"<div class='message'><div class='meta'><strong>{escaped_username}</strong> @ {created}</div>"
                f"<div class='content'>{escaped_content}</div></div>"
            )
    else:
        html_parts.append("<p>No messages found.</p>")

    # Simple form to post a new message
    html_parts.extend(
        [
            "<hr>",
            "<h2>Post a new message</h2>",
            "<form method='post' action='/messages' onsubmit='submitForm(event)'>",
            "<label>Username: <input type='text' name='username' required></label><br><br>",
            "<label>Content:<br><textarea name='content' rows='4' cols='50' required></textarea></label><br><br>",
            "<button type='submit'>Send</button>",
            "</form>",
            "<script>",
            "async function submitForm(e){",
            " e.preventDefault();",
            " const form = e.target;",
            " const data = {username: form.username.value, content: form.content.value};",
            " const resp = await fetch('/messages', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data)});",
            " if(resp.ok){ location.reload(); } else { alert('Failed to post message'); }",
            "}",
            "</script>",
            "</body>",
            "</html>",
        ]
    )
    html_content = "\n".join(html_parts)
    return HTMLResponse(content=html_content, status_code=200)


@app.post(
    "/messages",
    summary="Post a new message to the forum",
    status_code=200,
)
def post_message(request: Request, message: MessageIn):
    """
    Accepts a JSON payload with `username` and `content`, stores it, and returns a success response.
    """
    db = request.state.db
    cursor = db.cursor()
    now_iso = datetime.utcnow().isoformat(timespec="seconds")
    try:
        cursor.execute(
            "INSERT INTO messages (username, content, created_at) VALUES (?, ?, ?)",
            (message.username, message.content, now_iso),
        )
        db.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Database error")
    return JSONResponse(content={"detail": "Message posted successfully"}, status_code=200)


# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000, log_level="info")