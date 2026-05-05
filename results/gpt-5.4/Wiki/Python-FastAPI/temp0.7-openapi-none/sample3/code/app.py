import difflib
import html
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict


DB_PATH = "db.sqlite3"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "entry"


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
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
                content_before TEXT,
                content_after TEXT NOT NULL,
                modified_by TEXT NOT NULL,
                modified_at TEXT NOT NULL,
                summary TEXT NOT NULL,
                FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_edits_entry_id
            ON edits(entry_id)
            """
        )


def generate_unique_entry_id(conn: sqlite3.Connection, title: str) -> str:
    base = slugify(title)
    candidate = base
    counter = 2
    while True:
        row = conn.execute("SELECT 1 FROM entries WHERE id = ?", (candidate,)).fetchone()
        if row is None:
            return candidate
        candidate = f"{base}-{counter}"
        counter += 1


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    content: str
    lastModifiedBy: str
    lastModifiedAt: str


class NewEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    content: str
    createdBy: str


class UpdateEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    modifiedBy: str
    summary: str


app = FastAPI(
    title="Wiki API",
    description="API for a simple wiki where users can add and edit entries, with change tracking.",
    version="1.0.0",
)


@app.on_event("startup")
def startup() -> None:
    init_db()


def entry_row_to_model(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        lastModifiedBy=row["last_modified_by"],
        lastModifiedAt=row["last_modified_at"],
    )


def html_page(title: str, body: str) -> HTMLResponse:
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      max-width: 960px;
      margin: 2rem auto;
      padding: 0 1rem;
      line-height: 1.5;
      color: #222;
    }}
    a {{ color: #0b57d0; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .meta {{
      color: #666;
      font-size: 0.95rem;
      margin-bottom: 1rem;
    }}
    .entry-list li {{
      margin-bottom: 0.5rem;
    }}
    pre {{
      background: #f6f8fa;
      padding: 1rem;
      overflow-x: auto;
      border-radius: 6px;
      white-space: pre-wrap;
      word-wrap: break-word;
    }}
    .edit {{
      border: 1px solid #ddd;
      border-radius: 8px;
      padding: 1rem;
      margin-bottom: 1rem;
      background: #fff;
    }}
    .summary {{
      font-weight: bold;
      margin-bottom: 0.5rem;
    }}
    .diff {{
      background: #f6f8fa;
      border-radius: 6px;
      padding: 1rem;
      overflow-x: auto;
      white-space: pre-wrap;
      word-wrap: break-word;
    }}
    .nav {{
      margin-bottom: 1.5rem;
    }}
  </style>
</head>
<body>
  {body}
</body>
</html>"""
    return HTMLResponse(content=page)


@app.get("/entries", response_class=HTMLResponse)
def list_entries() -> HTMLResponse:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, title, last_modified_at
            FROM entries
            ORDER BY title COLLATE NOCASE ASC
            """
        ).fetchall()

    items = []
    for row in rows:
        entry_id = html.escape(row["id"])
        title = html.escape(row["title"])
        modified = html.escape(row["last_modified_at"])
        items.append(
            f'<li><a href="/entries/{entry_id}">{title}</a> '
            f'<span class="meta">(last updated: {modified})</span></li>'
        )

    body = (
        '<div class="nav"><a href="/entries">All Entries</a></div>'
        "<h1>Wiki Entries</h1>"
        + ("<ul class=\"entry-list\">" + "".join(items) + "</ul>" if items else "<p>No entries yet.</p>")
    )
    return html_page("Wiki Entries", body)


@app.post("/entries", response_model=Entry, status_code=201)
def create_entry(payload: NewEntry = Body(...)) -> Entry:
    now = utc_now_iso()
    with get_db() as conn:
        entry_id = generate_unique_entry_id(conn, payload.title)
        conn.execute(
            """
            INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (entry_id, payload.title, payload.content, payload.createdBy, now),
        )
        conn.execute(
            """
            INSERT INTO edits (entry_id, content_before, content_after, modified_by, modified_at, summary)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (entry_id, None, payload.content, payload.createdBy, now, "Initial creation"),
        )

    return Entry(
        id=entry_id,
        title=payload.title,
        content=payload.content,
        lastModifiedBy=payload.createdBy,
        lastModifiedAt=now,
    )


@app.get("/entries/{entryId}", response_class=HTMLResponse)
def get_entry(entryId: str) -> HTMLResponse:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, title, content, last_modified_by, last_modified_at
            FROM entries
            WHERE id = ?
            """,
            (entryId,),
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Entry not found")

        contributors_rows = conn.execute(
            """
            SELECT DISTINCT modified_by
            FROM edits
            WHERE entry_id = ?
            ORDER BY modified_by COLLATE NOCASE ASC
            """,
            (entryId,),
        ).fetchall()

    contributors: List[str] = [r["modified_by"] for r in contributors_rows]
    body = f"""
    <div class="nav">
      <a href="/entries">All Entries</a> |
      <a href="/entries/{html.escape(row["id"])}/edits">View Edit History</a>
    </div>
    <h1>{html.escape(row["title"])}</h1>
    <div class="meta">
      Last edited by <strong>{html.escape(row["last_modified_by"])}</strong>
      on <strong>{html.escape(row["last_modified_at"])}</strong>
    </div>
    <div class="meta">
      Contributors: {html.escape(", ".join(contributors) if contributors else "None")}
    </div>
    <pre>{html.escape(row["content"])}</pre>
    """
    return html_page(row["title"], body)


@app.put("/entries/{entryId}", response_model=Entry)
def update_entry(entryId: str, payload: UpdateEntry = Body(...)) -> Entry:
    now = utc_now_iso()
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, title, content
            FROM entries
            WHERE id = ?
            """,
            (entryId,),
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Entry not found")

        previous_content = row["content"]

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
            INSERT INTO edits (entry_id, content_before, content_after, modified_by, modified_at, summary)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                entryId,
                previous_content,
                payload.content,
                payload.modifiedBy,
                now,
                payload.summary,
            ),
        )

        updated = conn.execute(
            """
            SELECT id, title, content, last_modified_by, last_modified_at
            FROM entries
            WHERE id = ?
            """,
            (entryId,),
        ).fetchone()

    return entry_row_to_model(updated)


@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
def get_entry_edits(entryId: str) -> HTMLResponse:
    with get_db() as conn:
        entry = conn.execute(
            "SELECT id, title FROM entries WHERE id = ?",
            (entryId,),
        ).fetchone()

        if entry is None:
            raise HTTPException(status_code=404, detail="Entry not found")

        edits = conn.execute(
            """
            SELECT modified_by, modified_at, summary, content_before, content_after
            FROM edits
            WHERE entry_id = ?
            ORDER BY id DESC
            """,
            (entryId,),
        ).fetchall()

    edit_blocks = []
    for edit in edits:
        before = "" if edit["content_before"] is None else edit["content_before"]
        after = edit["content_after"]
        diff_lines = list(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile="before",
                tofile="after",
                lineterm="",
            )
        )
        diff_text = "\n".join(diff_lines) if diff_lines else "No changes"
        edit_blocks.append(
            f"""
            <div class="edit">
              <div class="summary">{html.escape(edit["summary"])}</div>
              <div class="meta">
                By <strong>{html.escape(edit["modified_by"])}</strong>
                on <strong>{html.escape(edit["modified_at"])}</strong>
              </div>
              <pre class="diff">{html.escape(diff_text)}</pre>
            </div>
            """
        )

    body = f"""
    <div class="nav">
      <a href="/entries">All Entries</a> |
      <a href="/entries/{html.escape(entry["id"])}">Back to Entry</a>
    </div>
    <h1>Edit History: {html.escape(entry["title"])}</h1>
    {''.join(edit_blocks) if edit_blocks else '<p>No edits found.</p>'}
    """
    return html_page(f"Edit History - {entry['title']}", body)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    accepts = request.headers.get("accept", "")
    if "text/html" in accepts:
        body = f"""
        <div class="nav"><a href="/entries">All Entries</a></div>
        <h1>Error {exc.status_code}</h1>
        <p>{html.escape(str(exc.detail))}</p>
        """
        return html_page(f"Error {exc.status_code}", body)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)