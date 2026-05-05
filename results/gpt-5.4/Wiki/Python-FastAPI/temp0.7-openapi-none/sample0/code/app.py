import html
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import HtmlDiff
from typing import Generator, List

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict


DB_PATH = "db.sqlite3"

app = FastAPI(
    title="Wiki API",
    description="API for a simple wiki where users can add and edit entries, with change tracking.",
    version="1.0.0",
)


class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str
    lastModifiedAt: datetime

    model_config = ConfigDict(from_attributes=True)


class NewEntry(BaseModel):
    title: str
    content: str
    createdBy: str


class UpdateEntry(BaseModel):
    content: str
    modifiedBy: str
    summary: str


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
                content_before TEXT NOT NULL,
                content_after TEXT NOT NULL,
                modified_by TEXT NOT NULL,
                summary TEXT NOT NULL,
                modified_at TEXT NOT NULL,
                FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
            )
            """
        )


@app.on_event("startup")
def startup() -> None:
    init_db()


def row_to_entry(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        lastModifiedBy=row["last_modified_by"],
        lastModifiedAt=datetime.fromisoformat(row["last_modified_at"]),
    )


def get_entry_or_404(conn: sqlite3.Connection, entry_id: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT id, title, content, last_modified_by, last_modified_at
        FROM entries
        WHERE id = ?
        """,
        (entry_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    return row


def get_contributors(conn: sqlite3.Connection, entry_id: str) -> List[str]:
    contributors = set()

    entry_row = conn.execute(
        "SELECT last_modified_by FROM entries WHERE id = ?",
        (entry_id,),
    ).fetchone()
    if entry_row:
        contributors.add(entry_row["last_modified_by"])

    edit_rows = conn.execute(
        "SELECT modified_by FROM edits WHERE entry_id = ? ORDER BY modified_at ASC",
        (entry_id,),
    ).fetchall()
    for row in edit_rows:
        contributors.add(row["modified_by"])

    return sorted(contributors)


def page_template(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      margin: 2rem auto;
      max-width: 960px;
      padding: 0 1rem;
      line-height: 1.5;
      color: #222;
    }}
    h1, h2, h3 {{
      color: #111;
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
      margin-bottom: 1rem;
    }}
    .content {{
      white-space: pre-wrap;
      border: 1px solid #ddd;
      padding: 1rem;
      background: #fafafa;
      border-radius: 6px;
    }}
    ul {{
      padding-left: 1.25rem;
    }}
    table.diff {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.9rem;
      margin-top: 1rem;
    }}
    .diff_header {{
      background: #f0f0f0;
    }}
    td, th {{
      padding: 0.35rem;
      vertical-align: top;
    }}
    .edit-block {{
      border: 1px solid #ddd;
      border-radius: 6px;
      padding: 1rem;
      margin-bottom: 1.25rem;
      background: #fff;
    }}
    .summary {{
      font-weight: bold;
    }}
    .nav {{
      margin-bottom: 1.5rem;
    }}
  </style>
</head>
<body>
  <div class="nav"><a href="/entries">All Entries</a></div>
  {body}
</body>
</html>"""


@app.get("/entries", response_class=HTMLResponse)
def list_entries() -> HTMLResponse:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, title
            FROM entries
            ORDER BY title COLLATE NOCASE ASC, id ASC
            """
        ).fetchall()

    items = []
    for row in rows:
        entry_id = html.escape(row["id"])
        title = html.escape(row["title"])
        items.append(f'<li><a href="/entries/{entry_id}">{title}</a></li>')

    body = f"""
    <h1>Wiki Entries</h1>
    <p>Total entries: {len(rows)}</p>
    <ul>
      {''.join(items) if items else '<li>No entries yet.</li>'}
    </ul>
    """
    return HTMLResponse(content=page_template("Wiki Entries", body), status_code=200)


@app.post("/entries", response_model=Entry, status_code=201)
def create_entry(payload: NewEntry) -> Entry:
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

    return Entry(
        id=entry_id,
        title=payload.title,
        content=payload.content,
        lastModifiedBy=payload.createdBy,
        lastModifiedAt=datetime.fromisoformat(now),
    )


@app.get("/entries/{entryId}", response_class=HTMLResponse)
def get_entry(entryId: str) -> HTMLResponse:
    with get_db() as conn:
        row = get_entry_or_404(conn, entryId)
        contributors = get_contributors(conn, entryId)

    title = html.escape(row["title"])
    content = html.escape(row["content"])
    last_modified_by = html.escape(row["last_modified_by"])
    last_modified_at = html.escape(row["last_modified_at"])
    contributors_html = "".join(f"<li>{html.escape(name)}</li>" for name in contributors) or "<li>None</li>"

    body = f"""
    <h1>{title}</h1>
    <div class="meta">
      <div><strong>Entry ID:</strong> {html.escape(row["id"])}</div>
      <div><strong>Last edited by:</strong> {last_modified_by}</div>
      <div><strong>Last edited at:</strong> {last_modified_at}</div>
      <div><a href="/entries/{html.escape(row['id'])}/edits">View edit history</a></div>
    </div>
    <h2>Content</h2>
    <div class="content">{content}</div>
    <h2>Contributors</h2>
    <ul>{contributors_html}</ul>
    """
    return HTMLResponse(content=page_template(row["title"], body), status_code=200)


@app.put("/entries/{entryId}", response_model=Entry)
def update_entry(entryId: str, payload: UpdateEntry) -> Entry:
    with get_db() as conn:
        row = get_entry_or_404(conn, entryId)
        previous_content = row["content"]
        now = utc_now_iso()

        conn.execute(
            """
            INSERT INTO edits (entry_id, content_before, content_after, modified_by, summary, modified_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                entryId,
                previous_content,
                payload.content,
                payload.modifiedBy,
                payload.summary,
                now,
            ),
        )

        conn.execute(
            """
            UPDATE entries
            SET content = ?, last_modified_by = ?, last_modified_at = ?
            WHERE id = ?
            """,
            (payload.content, payload.modifiedBy, now, entryId),
        )

        updated_row = conn.execute(
            """
            SELECT id, title, content, last_modified_by, last_modified_at
            FROM entries
            WHERE id = ?
            """,
            (entryId,),
        ).fetchone()

    return row_to_entry(updated_row)


@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
def get_edit_history(entryId: str) -> HTMLResponse:
    with get_db() as conn:
        entry_row = get_entry_or_404(conn, entryId)
        edit_rows = conn.execute(
            """
            SELECT id, content_before, content_after, modified_by, summary, modified_at
            FROM edits
            WHERE entry_id = ?
            ORDER BY modified_at DESC, id DESC
            """,
            (entryId,),
        ).fetchall()

    diff_tool = HtmlDiff(wrapcolumn=80)
    edit_blocks = []

    for row in edit_rows:
        before_lines = row["content_before"].splitlines()
        after_lines = row["content_after"].splitlines()
        diff_html = diff_tool.make_table(
            before_lines,
            after_lines,
            fromdesc="Before",
            todesc="After",
            context=True,
            numlines=3,
        )
        edit_blocks.append(
            f"""
            <div class="edit-block">
              <div><strong>Edited by:</strong> {html.escape(row["modified_by"])}</div>
              <div><strong>Edited at:</strong> {html.escape(row["modified_at"])}</div>
              <div class="summary"><strong>Summary:</strong> {html.escape(row["summary"])}</div>
              {diff_html}
            </div>
            """
        )

    body = f"""
    <h1>Edit History: {html.escape(entry_row["title"])}</h1>
    <p><a href="/entries/{html.escape(entryId)}">Back to entry</a></p>
    {''.join(edit_blocks) if edit_blocks else '<p>No edits have been recorded for this entry.</p>'}
    """
    return HTMLResponse(content=page_template(f"Edit History - {entry_row['title']}", body), status_code=200)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> Response:
    if exc.status_code == 404:
        accepts_html = "text/html" in request.headers.get("accept", "")
        if accepts_html or request.url.path.startswith("/entries"):
            body = f"""
            <h1>Not Found</h1>
            <p>{html.escape(str(exc.detail))}</p>
            """
            return HTMLResponse(content=page_template("Not Found", body), status_code=404)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)