import html
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import HtmlDiff
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(title: str) -> str:
    slug = title.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "entry"


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
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
                content TEXT NOT NULL,
                modified_by TEXT NOT NULL,
                modified_at TEXT NOT NULL,
                summary TEXT NOT NULL,
                FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_edits_entry_id ON edits(entry_id)"
        )


@app.on_event("startup")
def startup() -> None:
    init_db()


def generate_unique_entry_id(conn: sqlite3.Connection, title: str) -> str:
    base = slugify(title)
    candidate = base
    counter = 2
    while conn.execute("SELECT 1 FROM entries WHERE id = ?", (candidate,)).fetchone():
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


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
        "SELECT id, title, content, last_modified_by, last_modified_at FROM entries WHERE id = ?",
        (entry_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    return row


def get_contributors(conn: sqlite3.Connection, entry_id: str) -> List[str]:
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


def render_page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{
      font-family: Arial, sans-serif;
      margin: 2rem auto;
      max-width: 960px;
      padding: 0 1rem;
      color: #222;
      line-height: 1.5;
    }}
    a {{
      color: #0b57d0;
      text-decoration: none;
    }}
    a:hover {{
      text-decoration: underline;
    }}
    h1, h2, h3 {{
      line-height: 1.2;
    }}
    .meta {{
      color: #666;
      font-size: 0.95rem;
      margin-bottom: 1rem;
    }}
    .content {{
      white-space: pre-wrap;
      border: 1px solid #ddd;
      background: #fafafa;
      padding: 1rem;
      border-radius: 6px;
    }}
    .entry-list li {{
      margin: 0.4rem 0;
    }}
    .contributors {{
      margin-top: 1rem;
      padding: 0.75rem 1rem;
      background: #f5f7fb;
      border: 1px solid #dce3f0;
      border-radius: 6px;
    }}
    .edit-card {{
      border: 1px solid #ddd;
      border-radius: 6px;
      padding: 1rem;
      margin: 1rem 0 2rem 0;
      background: #fff;
    }}
    .summary {{
      font-weight: bold;
    }}
    table.diff {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.9rem;
      margin-top: 1rem;
    }}
    .diff_header {{
      background: #efefef;
    }}
    td, th {{
      padding: 0.35rem;
      vertical-align: top;
    }}
    .diff_add {{
      background: #d6ffd6;
    }}
    .diff_sub {{
      background: #ffd6d6;
    }}
    .diff_chg {{
      background: #fff0b3;
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
            "SELECT id, title FROM entries ORDER BY title COLLATE NOCASE ASC"
        ).fetchall()

    if rows:
        items = "\n".join(
            f'<li><a href="/entries/{html.escape(row["id"])}">{html.escape(row["title"])}</a></li>'
            for row in rows
        )
        body = f"""
        <h1>Wiki Entries</h1>
        <ul class="entry-list">
          {items}
        </ul>
        """
    else:
        body = """
        <h1>Wiki Entries</h1>
        <p>No entries found.</p>
        """

    return HTMLResponse(content=render_page("Wiki Entries", body), status_code=200)


@app.post("/entries", response_model=Entry, status_code=201)
def create_entry(payload: NewEntry) -> Entry:
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
            INSERT INTO edits (entry_id, content, modified_by, modified_at, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (entry_id, payload.content, payload.createdBy, now, "Initial creation"),
        )
        row = conn.execute(
            "SELECT id, title, content, last_modified_by, last_modified_at FROM entries WHERE id = ?",
            (entry_id,),
        ).fetchone()

    return row_to_entry(row)


@app.get("/entries/{entryId}", response_class=HTMLResponse)
def get_entry(entryId: str) -> HTMLResponse:
    with get_db() as conn:
        row = get_entry_or_404(conn, entryId)
        contributors = get_contributors(conn, entryId)

    contributors_html = (
        "<ul>" + "".join(f"<li>{html.escape(name)}</li>" for name in contributors) + "</ul>"
        if contributors
        else "<p>No contributors recorded.</p>"
    )

    body = f"""
    <h1>{html.escape(row["title"])}</h1>
    <div class="meta">
      Last edited by <strong>{html.escape(row["last_modified_by"])}</strong>
      on <strong>{html.escape(row["last_modified_at"])}</strong>
    </div>
    <div class="content">{html.escape(row["content"])}</div>
    <div class="contributors">
      <h2>Contributors</h2>
      {contributors_html}
    </div>
    <p><a href="/entries/{html.escape(row["id"])}/edits">View edit history</a></p>
    """
    return HTMLResponse(content=render_page(row["title"], body), status_code=200)


@app.put("/entries/{entryId}", response_model=Entry)
def update_entry(entryId: str, payload: UpdateEntry) -> Entry:
    with get_db() as conn:
        existing = get_entry_or_404(conn, entryId)
        now = utc_now_iso()

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
            (entryId, payload.content, payload.modifiedBy, now, payload.summary),
        )
        row = conn.execute(
            "SELECT id, title, content, last_modified_by, last_modified_at FROM entries WHERE id = ?",
            (entryId,),
        ).fetchone()

    return row_to_entry(row)


@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
def get_edit_history(entryId: str) -> HTMLResponse:
    with get_db() as conn:
        entry = get_entry_or_404(conn, entryId)
        edits = conn.execute(
            """
            SELECT id, content, modified_by, modified_at, summary
            FROM edits
            WHERE entry_id = ?
            ORDER BY id ASC
            """,
            (entryId,),
        ).fetchall()

    diff_tool = HtmlDiff(wrapcolumn=80)
    sections = []
    previous_content: Optional[str] = ""

    for index, edit in enumerate(edits, start=1):
        prev_lines = (previous_content or "").splitlines()
        curr_lines = (edit["content"] or "").splitlines()
        diff_html = diff_tool.make_table(
            prev_lines,
            curr_lines,
            fromdesc="Previous",
            todesc="Current",
            context=True,
            numlines=2,
        )
        section = f"""
        <div class="edit-card">
          <h2>Edit {index}</h2>
          <div><span class="summary">Summary:</span> {html.escape(edit["summary"])}</div>
          <div><strong>Contributor:</strong> {html.escape(edit["modified_by"])}</div>
          <div><strong>Modified at:</strong> {html.escape(edit["modified_at"])}</div>
          {diff_html}
        </div>
        """
        sections.append(section)
        previous_content = edit["content"]

    if not sections:
        sections_html = "<p>No edits found.</p>"
    else:
        sections_html = "\n".join(sections)

    body = f"""
    <h1>Edit History: {html.escape(entry["title"])}</h1>
    <p><a href="/entries/{html.escape(entry["id"])}">Back to entry</a></p>
    {sections_html}
    """
    return HTMLResponse(content=render_page(f"Edit History - {entry['title']}", body), status_code=200)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 404:
        accepts_html = "text/html" in request.headers.get("accept", "")
        if accepts_html:
            body = f"""
            <h1>404 - Not Found</h1>
            <p>{html.escape(str(exc.detail))}</p>
            """
            return HTMLResponse(content=render_page("Not Found", body), status_code=404)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)