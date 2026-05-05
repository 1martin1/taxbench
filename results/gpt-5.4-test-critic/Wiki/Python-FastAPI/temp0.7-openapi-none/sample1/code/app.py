import difflib
import html
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List

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


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(title: str) -> str:
    value = title.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "entry"


def ensure_unique_slug(conn: sqlite3.Connection, title: str) -> str:
    base = slugify(title)
    candidate = base
    counter = 2
    while True:
        row = conn.execute("SELECT 1 FROM entries WHERE id = ?", (candidate,)).fetchone()
        if row is None:
            return candidate
        candidate = f"{base}-{counter}"
        counter += 1


def init_db():
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
                FOREIGN KEY (entry_id) REFERENCES entries (id) ON DELETE CASCADE
            )
            """
        )


@app.on_event("startup")
def startup():
    init_db()


def fetch_entry_or_404(conn: sqlite3.Connection, entry_id: str) -> sqlite3.Row:
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
    rows = conn.execute(
        """
        SELECT DISTINCT modified_by
        FROM edits
        WHERE entry_id = ?
        ORDER BY modified_by
        """,
        (entry_id,),
    ).fetchall()
    return [row["modified_by"] for row in rows]


def render_page(title: str, body: str) -> str:
    safe_title = html.escape(title)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{safe_title}</title>
<style>
body {{
    font-family: Arial, sans-serif;
    margin: 2rem auto;
    max-width: 960px;
    padding: 0 1rem;
    line-height: 1.5;
    color: #222;
}}
a {{ color: #0b57d0; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
h1, h2, h3 {{ margin-top: 1.5rem; }}
pre, code {{
    background: #f4f4f4;
    border-radius: 4px;
}}
pre {{
    padding: 1rem;
    overflow-x: auto;
}}
.meta {{
    color: #555;
    margin-bottom: 1rem;
}}
ul {{
    padding-left: 1.2rem;
}}
table {{
    border-collapse: collapse;
    width: 100%;
    margin-top: 1rem;
}}
th, td {{
    border: 1px solid #ccc;
    padding: 0.6rem;
    vertical-align: top;
}}
th {{
    background: #f5f5f5;
}}
.diff {{
    white-space: pre-wrap;
    font-family: monospace;
    background: #fafafa;
    border: 1px solid #ddd;
    padding: 1rem;
    margin-top: 0.5rem;
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
def list_entries():
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
        last_modified_at = html.escape(row["last_modified_at"])
        items.append(
            f'<li><a href="/entries/{entry_id}">{title}</a> '
            f'<small>(last updated: {last_modified_at})</small></li>'
        )

    body = (
        "<h1>Wiki Entries</h1>"
        "<p>This wiki supports creating and editing entries with change tracking.</p>"
        + ("<ul>" + "".join(items) + "</ul>" if items else "<p>No entries yet.</p>")
    )
    return HTMLResponse(content=render_page("Wiki Entries", body), status_code=200)


@app.post("/entries", response_model=Entry, status_code=201)
def create_entry(payload: NewEntry):
    now = utc_now_iso()
    with get_db() as conn:
        entry_id = ensure_unique_slug(conn, payload.title)
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
        row = fetch_entry_or_404(conn, entry_id)

    return Entry(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        lastModifiedBy=row["last_modified_by"],
        lastModifiedAt=datetime.fromisoformat(row["last_modified_at"]),
    )


@app.get("/entries/{entryId}", response_class=HTMLResponse)
def get_entry(entryId: str):
    with get_db() as conn:
        row = fetch_entry_or_404(conn, entryId)
        contributors = get_contributors(conn, entryId)

    title = html.escape(row["title"])
    content = html.escape(row["content"])
    last_modified_by = html.escape(row["last_modified_by"])
    last_modified_at = html.escape(row["last_modified_at"])
    contributors_html = "".join(f"<li>{html.escape(name)}</li>" for name in contributors)

    body = f"""
    <h1>{title}</h1>
    <div class="meta">
        <div><strong>Last edited by:</strong> {last_modified_by}</div>
        <div><strong>Last edited at:</strong> {last_modified_at}</div>
    </div>
    <h2>Content</h2>
    <pre>{content}</pre>
    <h2>Contributors</h2>
    {"<ul>" + contributors_html + "</ul>" if contributors else "<p>No contributors recorded.</p>"}
    <p><a href="/entries/{html.escape(entryId)}/edits">View edit history</a></p>
    """
    return HTMLResponse(content=render_page(row["title"], body), status_code=200)


@app.put("/entries/{entryId}", response_model=Entry)
def update_entry(entryId: str, payload: UpdateEntry):
    now = utc_now_iso()
    with get_db() as conn:
        row = fetch_entry_or_404(conn, entryId)
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
        updated = fetch_entry_or_404(conn, entryId)

    return Entry(
        id=updated["id"],
        title=updated["title"],
        content=updated["content"],
        lastModifiedBy=updated["last_modified_by"],
        lastModifiedAt=datetime.fromisoformat(updated["last_modified_at"]),
    )


@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
def get_edit_history(entryId: str):
    with get_db() as conn:
        entry = fetch_entry_or_404(conn, entryId)
        edits = conn.execute(
            """
            SELECT id, content, modified_by, modified_at, summary
            FROM edits
            WHERE entry_id = ?
            ORDER BY modified_at ASC, id ASC
            """,
            (entryId,),
        ).fetchall()

    sections = [f"<h1>Edit History: {html.escape(entry['title'])}</h1>"]

    if not edits:
        sections.append("<p>No edits found.</p>")
    else:
        previous_content = ""
        for index, edit in enumerate(edits, start=1):
            current_content = edit["content"]
            diff_lines = difflib.unified_diff(
                previous_content.splitlines(),
                current_content.splitlines(),
                fromfile="previous",
                tofile="current",
                lineterm="",
            )
            diff_text = "\n".join(diff_lines) or "No textual changes detected."
            sections.append(
                f"""
                <div>
                    <h2>Edit {index}</h2>
                    <p><strong>Contributor:</strong> {html.escape(edit["modified_by"])}</p>
                    <p><strong>Date:</strong> {html.escape(edit["modified_at"])}</p>
                    <p><strong>Summary:</strong> {html.escape(edit["summary"])}</p>
                    <div class="diff">{html.escape(diff_text)}</div>
                </div>
                <hr>
                """
            )
            previous_content = current_content

    return HTMLResponse(
        content=render_page(f"Edit History - {entry['title']}", "".join(sections)),
        status_code=200,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if request.method == "GET" and request.url.path.startswith("/entries"):
        if exc.status_code == 404:
            body = render_page(
                "Not Found",
                "<h1>404 - Entry not found</h1><p>The requested wiki entry does not exist.</p>",
            )
            return HTMLResponse(content=body, status_code=404)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)