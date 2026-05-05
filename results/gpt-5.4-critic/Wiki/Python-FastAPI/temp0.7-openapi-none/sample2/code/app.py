import html
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import unified_diff
from typing import Generator, List, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

DB_PATH = "db.sqlite3"

MAX_TITLE_LENGTH = 200
MAX_CONTENT_LENGTH = 100_000
MAX_USER_LENGTH = 100
MAX_SUMMARY_LENGTH = 500
MAX_ENTRY_LIST_ITEMS = 500
MAX_EDIT_HISTORY_ITEMS = 200
MAX_DIFF_CONTENT_CHARS = 20_000
SQLITE_BUSY_TIMEOUT_MS = 5000
AUTH_TOKEN = os.getenv("WIKI_AUTH_TOKEN", "change-me")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
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
            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                last_modified_by TEXT NOT NULL,
                last_modified_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS edits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT NOT NULL,
                content TEXT NOT NULL,
                modified_by TEXT NOT NULL,
                modified_at TEXT NOT NULL,
                summary TEXT NOT NULL,
                FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_edits_entry_id ON edits(entry_id)"
        )


class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str
    lastModifiedAt: str


class NewEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=MAX_TITLE_LENGTH)
    content: str = Field(..., max_length=MAX_CONTENT_LENGTH)
    createdBy: str = Field(..., min_length=1, max_length=MAX_USER_LENGTH)


class UpdateEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., max_length=MAX_CONTENT_LENGTH)
    modifiedBy: str = Field(..., min_length=1, max_length=MAX_USER_LENGTH)

    @model_validator(mode="before")
    @classmethod
    def reject_summary_if_present(cls, data):
        if isinstance(data, dict) and "summary" in data:
            raise ValueError("summary is not allowed by this API schema")
        return data


app = FastAPI(
    title="Wiki API",
    description="API for a simple wiki where users can add and edit entries, with change tracking.",
    version="1.0.0",
)


def require_auth(x_auth_token: Optional[str]) -> None:
    if x_auth_token != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def row_to_entry(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        lastModifiedBy=row["last_modified_by"],
        lastModifiedAt=row["last_modified_at"],
    )


def fetch_entry(conn: sqlite3.Connection, entry_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, title, content, last_modified_by, last_modified_at
        FROM entries
        WHERE id = ?
        """,
        (entry_id,),
    ).fetchone()


def fetch_contributors(conn: sqlite3.Connection, entry_id: str) -> List[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT modified_by
        FROM edits
        WHERE entry_id = ?
        ORDER BY modified_by COLLATE NOCASE ASC
        """,
        (entry_id,),
    ).fetchall()
    return [row["modified_by"] for row in rows]


def html_page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      margin: 2rem;
      line-height: 1.5;
      color: #222;
    }}
    a {{
      color: #0b57d0;
      text-decoration: none;
    }}
    a:hover {{
      text-decoration: underline;
    }}
    .meta {{
      color: #666;
      font-size: 0.95rem;
    }}
    .entry-content {{
      white-space: pre-wrap;
      border: 1px solid #ddd;
      padding: 1rem;
      background: #fafafa;
      margin-top: 1rem;
    }}
    .edit {{
      border: 1px solid #ddd;
      padding: 1rem;
      margin-bottom: 1rem;
      background: #fff;
    }}
    pre.diff {{
      white-space: pre-wrap;
      background: #f6f8fa;
      border: 1px solid #ddd;
      padding: 1rem;
      overflow-x: auto;
    }}
    ul {{
      padding-left: 1.25rem;
    }}
  </style>
</head>
<body>
{body}
</body>
</html>"""


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/entries", response_class=HTMLResponse)
def list_entries() -> HTMLResponse:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, title
            FROM entries
            ORDER BY title COLLATE NOCASE ASC, id ASC
            LIMIT ?
            """,
            (MAX_ENTRY_LIST_ITEMS,),
        ).fetchall()

    items = []
    for row in rows:
        entry_id = html.escape(row["id"])
        title = html.escape(row["title"])
        items.append(f'<li><a href="/entries/{entry_id}">{title}</a></li>')

    body = f"""
    <h1>Wiki Entries</h1>
    <p class="meta">All entries with links to their pages.</p>
    <ul>
      {''.join(items) if items else '<li>No entries found.</li>'}
    </ul>
    """
    return HTMLResponse(content=html_page("Wiki Entries", body), status_code=200)


@app.post("/entries", response_model=Entry, status_code=201)
def create_entry(
    payload: NewEntry,
    x_auth_token: Optional[str] = Header(default=None, alias="X-Auth-Token"),
) -> Entry:
    require_auth(x_auth_token)
    entry_id = str(uuid.uuid4())
    now = utc_now_iso()

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (entry_id, payload.title, payload.content, payload.createdBy, now),
        )
        conn.execute(
            """
            INSERT INTO edits (entry_id, content, modified_by, modified_at, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                payload.content,
                payload.createdBy,
                now,
                "Initial creation",
            ),
        )
        row = fetch_entry(conn, entry_id)

    return row_to_entry(row)


@app.get("/entries/{entryId}", response_class=HTMLResponse)
def get_entry(entryId: str) -> HTMLResponse:
    with get_db() as conn:
        row = fetch_entry(conn, entryId)
        if row is None:
            raise HTTPException(status_code=404, detail="Entry not found")
        contributors = fetch_contributors(conn, entryId)

    title = html.escape(row["title"])
    content = html.escape(row["content"])
    modified_by = html.escape(row["last_modified_by"])
    modified_at = html.escape(row["last_modified_at"])
    contributors_html = "".join(f"<li>{html.escape(c)}</li>" for c in contributors)

    body = f"""
    <h1>{title}</h1>
    <p class="meta">Last edited by <strong>{modified_by}</strong> at <strong>{modified_at}</strong></p>
    <p><a href="/entries">Back to all entries</a> | <a href="/entries/{html.escape(entryId)}/edits">View edit history</a></p>
    <h2>Contributors</h2>
    <ul>
      {contributors_html if contributors_html else '<li>No contributors recorded.</li>'}
    </ul>
    <h2>Content</h2>
    <div class="entry-content">{content}</div>
    """
    return HTMLResponse(content=html_page(row["title"], body), status_code=200)


@app.put("/entries/{entryId}", response_model=Entry)
def update_entry(
    entryId: str,
    payload: UpdateEntry,
    x_auth_token: Optional[str] = Header(default=None, alias="X-Auth-Token"),
) -> Entry:
    require_auth(x_auth_token)
    now = utc_now_iso()

    with get_db() as conn:
        existing = fetch_entry(conn, entryId)
        if existing is None:
            raise HTTPException(status_code=404, detail="Entry not found")

        conn.execute(
            """
            UPDATE entries
            SET content = ?, last_modified_by = ?, last_modified_at = ?
            WHERE id = ?
            """,
            (payload.content, payload.modifiedBy, now, entryId),
        )
        conn.execute(
            """
            INSERT INTO edits (entry_id, content, modified_by, modified_at, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (entryId, payload.content, payload.modifiedBy, now, ""),
        )
        updated = fetch_entry(conn, entryId)

    return row_to_entry(updated)


@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
def get_entry_edits(entryId: str) -> HTMLResponse:
    with get_db() as conn:
        entry = fetch_entry(conn, entryId)
        if entry is None:
            raise HTTPException(status_code=404, detail="Entry not found")

        edits = conn.execute(
            """
            SELECT id, content, modified_by, modified_at, summary
            FROM edits
            WHERE entry_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (entryId, MAX_EDIT_HISTORY_ITEMS),
        ).fetchall()

    edit_blocks = []
    previous_content = ""
    for idx, edit in enumerate(edits, start=1):
        current_content = edit["content"]
        if (
            len(previous_content) > MAX_DIFF_CONTENT_CHARS
            or len(current_content) > MAX_DIFF_CONTENT_CHARS
        ):
            diff_text = "Diff omitted because content is too large."
        else:
            diff_text = "\n".join(
                unified_diff(
                    previous_content.splitlines(),
                    current_content.splitlines(),
                    fromfile="previous",
                    tofile="current",
                    lineterm="",
                )
            )
            if not diff_text:
                diff_text = "No textual changes detected."

        edit_blocks.append(
            f"""
            <div class="edit">
              <h2>Edit #{idx}</h2>
              <p class="meta">
                Contributor: <strong>{html.escape(edit["modified_by"])}</strong><br>
                Timestamp: <strong>{html.escape(edit["modified_at"])}</strong><br>
                Summary: <strong>{html.escape(edit["summary"])}</strong>
              </p>
              <h3>Diff</h3>
              <pre class="diff">{html.escape(diff_text)}</pre>
            </div>
            """
        )
        previous_content = current_content

    body = f"""
    <h1>Edit History: {html.escape(entry["title"])}</h1>
    <p><a href="/entries">Back to all entries</a> | <a href="/entries/{html.escape(entryId)}">Back to entry</a></p>
    {''.join(edit_blocks) if edit_blocks else '<p>No edits found.</p>'}
    """
    return HTMLResponse(content=html_page(f"Edit History - {entry['title']}", body), status_code=200)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)