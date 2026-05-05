import difflib
import html
import os
import re
import sqlite3
from contextlib import closing
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


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(title: str) -> str:
    value = title.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "entry"


def ensure_unique_slug(conn: sqlite3.Connection, base_slug: str) -> str:
    slug = base_slug
    counter = 2
    while True:
        row = conn.execute("SELECT 1 FROM entries WHERE id = ?", (slug,)).fetchone()
        if row is None:
            return slug
        slug = f"{base_slug}-{counter}"
        counter += 1


def init_db() -> None:
    with closing(get_db()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                last_modified_by TEXT NOT NULL,
                last_modified_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS edits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT NOT NULL,
                content_before TEXT NOT NULL,
                content_after TEXT NOT NULL,
                modified_by TEXT NOT NULL,
                modified_at TEXT NOT NULL,
                summary TEXT NOT NULL,
                FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_edits_entry_id ON edits(entry_id);
            """
        )
        conn.commit()


def html_page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
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
h1, h2, h3 {{ margin-top: 1.5rem; }}
.meta {{
    color: #555;
    font-size: 0.95rem;
    margin-bottom: 1rem;
}}
.entry-list li {{
    margin: 0.5rem 0;
}}
pre {{
    background: #f6f8fa;
    padding: 1rem;
    overflow-x: auto;
    border: 1px solid #ddd;
    border-radius: 6px;
}}
.diff {{
    background: #f6f8fa;
    border: 1px solid #ddd;
    border-radius: 6px;
    padding: 1rem;
    white-space: pre-wrap;
    font-family: monospace;
}}
.diff .add {{ color: #116329; }}
.diff .del {{ color: #a61b1b; }}
.diff .hdr {{ color: #555; font-weight: bold; }}
.edit {{
    border: 1px solid #ddd;
    border-radius: 6px;
    padding: 1rem;
    margin-bottom: 1rem;
}}
code {{
    background: #f1f3f4;
    padding: 0.1rem 0.3rem;
    border-radius: 4px;
}}
</style>
</head>
<body>
{body}
</body>
</html>"""


def format_datetime(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return value


def render_diff(before: str, after: str) -> str:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    lines = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile="before",
        tofile="after",
        lineterm="",
    )
    rendered = []
    for line in lines:
        escaped = html.escape(line)
        if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
            rendered.append(f'<div class="hdr">{escaped}</div>')
        elif line.startswith("+") and not line.startswith("+++"):
            rendered.append(f'<div class="add">{escaped}</div>')
        elif line.startswith("-") and not line.startswith("---"):
            rendered.append(f'<div class="del">{escaped}</div>')
        else:
            rendered.append(f"<div>{escaped}</div>")
    if not rendered:
        rendered.append("<div>No changes detected.</div>")
    return '<div class="diff">' + "".join(rendered) + "</div>"


def row_to_entry(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        lastModifiedBy=row["last_modified_by"],
        lastModifiedAt=datetime.fromisoformat(row["last_modified_at"]),
    )


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/entries", response_class=HTMLResponse)
def list_entries() -> HTMLResponse:
    with closing(get_db()) as conn:
        rows = conn.execute(
            "SELECT id, title FROM entries ORDER BY title COLLATE NOCASE ASC"
        ).fetchall()

    items = "".join(
        f'<li><a href="/entries/{html.escape(row["id"])}">{html.escape(row["title"])}</a> '
        f'(<code>/entries/{html.escape(row["id"])}</code>)</li>'
        for row in rows
    )

    body = f"""
    <h1>Wiki Entries</h1>
    <p>List of all entries with their titles and links.</p>
    <ul class="entry-list">
        {items or "<li>No entries found.</li>"}
    </ul>
    """
    return HTMLResponse(content=html_page("Wiki Entries", body), status_code=200)


@app.post("/entries", response_model=Entry, status_code=201)
def create_entry(payload: NewEntry) -> Entry:
    now = utc_now_iso()
    with closing(get_db()) as conn:
        base_slug = slugify(payload.title)
        entry_id = ensure_unique_slug(conn, base_slug)

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
            (entry_id, "", payload.content, payload.createdBy, now, "Initial creation"),
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM entries WHERE id = ?",
            (entry_id,),
        ).fetchone()

    return row_to_entry(row)


@app.get("/entries/{entryId}", response_class=HTMLResponse)
def get_entry(entryId: str) -> HTMLResponse:
    with closing(get_db()) as conn:
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (entryId,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Entry not found")

        contributor_rows = conn.execute(
            """
            SELECT DISTINCT modified_by
            FROM edits
            WHERE entry_id = ?
            ORDER BY modified_by COLLATE NOCASE ASC
            """,
            (entryId,),
        ).fetchall()

    contributors: List[str] = [r["modified_by"] for r in contributor_rows]
    body = f"""
    <h1>{html.escape(row["title"])}</h1>
    <div class="meta">
        <div><strong>Last edited by:</strong> {html.escape(row["last_modified_by"])}</div>
        <div><strong>Last edited at:</strong> {html.escape(format_datetime(row["last_modified_at"]))}</div>
        <div><strong>Contributors:</strong> {html.escape(", ".join(contributors) if contributors else "None")}</div>
        <div><a href="/entries/{html.escape(entryId)}/edits">View edit history</a></div>
    </div>
    <h2>Content</h2>
    <pre>{html.escape(row["content"])}</pre>
    """
    return HTMLResponse(content=html_page(row["title"], body), status_code=200)


@app.put("/entries/{entryId}", response_model=Entry)
def update_entry(entryId: str, payload: UpdateEntry) -> Entry:
    now = utc_now_iso()
    with closing(get_db()) as conn:
        existing = conn.execute(
            "SELECT * FROM entries WHERE id = ?",
            (entryId,),
        ).fetchone()
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
            INSERT INTO edits (entry_id, content_before, content_after, modified_by, modified_at, summary)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                entryId,
                existing["content"],
                payload.content,
                payload.modifiedBy,
                now,
                payload.summary,
            ),
        )
        conn.commit()

        row = conn.execute("SELECT * FROM entries WHERE id = ?", (entryId,)).fetchone()

    return row_to_entry(row)


@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
def get_entry_edits(entryId: str) -> HTMLResponse:
    with closing(get_db()) as conn:
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

    rendered_edits = []
    for edit in edits:
        rendered_edits.append(
            f"""
            <div class="edit">
                <div><strong>Contributor:</strong> {html.escape(edit["modified_by"])}</div>
                <div><strong>Edited at:</strong> {html.escape(format_datetime(edit["modified_at"]))}</div>
                <div><strong>Summary:</strong> {html.escape(edit["summary"])}</div>
                <h3>Diff</h3>
                {render_diff(edit["content_before"], edit["content_after"])}
            </div>
            """
        )

    body = f"""
    <h1>Edit History: {html.escape(entry["title"])}</h1>
    <p><a href="/entries/{html.escape(entryId)}">Back to entry</a></p>
    {"".join(rendered_edits) if rendered_edits else "<p>No edits found.</p>"}
    """
    return HTMLResponse(content=html_page(f'Edit History - {entry["title"]}', body), status_code=200)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if request.method == "GET":
        body = f"""
        <h1>Error {exc.status_code}</h1>
        <p>{html.escape(str(exc.detail))}</p>
        """
        return HTMLResponse(
            content=html_page(f"Error {exc.status_code}", body),
            status_code=exc.status_code,
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", "5000"))
    uvicorn.run(app, host="0.0.0.0", port=port)