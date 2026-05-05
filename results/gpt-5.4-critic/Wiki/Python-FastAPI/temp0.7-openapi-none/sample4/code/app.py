import html
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import HtmlDiff
from typing import Generator, List

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator


app = FastAPI(
    title="Wiki API",
    description="API for a simple wiki where users can add and edit entries, with change tracking.",
    version="1.0.0",
)

DB_PATH = "db.sqlite3"

MAX_TITLE_LENGTH = 200
MAX_CONTENT_LENGTH = 100_000
MAX_USER_LENGTH = 100
MAX_SUMMARY_LENGTH = 500
MAX_EDITS_RENDERED = 100
MAX_DIFF_TOTAL_CHARS = 500_000
WRITE_CONCURRENCY_LIMIT = 1

_write_semaphore = threading.BoundedSemaphore(WRITE_CONCURRENCY_LIMIT)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
                FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_title ON entries(title)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_edits_entry_id_id ON edits(entry_id, id)"
        )


@app.on_event("startup")
def startup() -> None:
    init_db()


class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str
    lastModifiedAt: str

    model_config = ConfigDict(populate_by_name=True)


class NewEntry(BaseModel):
    title: str = Field(..., min_length=1, max_length=MAX_TITLE_LENGTH)
    content: str = Field(..., min_length=1, max_length=MAX_CONTENT_LENGTH)
    createdBy: str = Field(..., min_length=1, max_length=MAX_USER_LENGTH)

    @model_validator(mode="after")
    def validate_not_blank(self) -> "NewEntry":
        if not self.title.strip():
            raise ValueError("title must not be blank")
        if not self.content.strip():
            raise ValueError("content must not be blank")
        if not self.createdBy.strip():
            raise ValueError("createdBy must not be blank")
        return self


class UpdateEntry(BaseModel):
    content: str = Field(..., min_length=1, max_length=MAX_CONTENT_LENGTH)
    modifiedBy: str = Field(..., min_length=1, max_length=MAX_USER_LENGTH)
    summary: str = Field(..., min_length=1, max_length=MAX_SUMMARY_LENGTH)

    @model_validator(mode="after")
    def validate_not_blank(self) -> "UpdateEntry":
        if not self.content.strip():
            raise ValueError("content must not be blank")
        if not self.modifiedBy.strip():
            raise ValueError("modifiedBy must not be blank")
        if not self.summary.strip():
            raise ValueError("summary must not be blank")
        return self

    @classmethod
    def model_json_schema(cls, by_alias: bool = True, ref_template: str = "#/$defs/{model}"):
        schema = super().model_json_schema(by_alias=by_alias, ref_template=ref_template)
        properties = schema.get("properties", {})
        if "summary" in properties:
            properties.pop("summary", None)
        required = schema.get("required")
        if isinstance(required, list) and "summary" not in required:
            required.append("summary")
        return schema


def slugify_title(title: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.strip().lower()).strip("-")
    return slug[:80] or f"entry-{uuid.uuid4().hex[:8]}"


def unique_entry_id(conn: sqlite3.Connection, title: str) -> str:
    base = slugify_title(title)
    like_pattern = f"{base}%"
    rows = conn.execute(
        """
        SELECT id
        FROM entries
        WHERE id = ? OR id LIKE ?
        """,
        (base, like_pattern),
    ).fetchall()
    existing_ids = {row["id"] for row in rows}
    if base not in existing_ids:
        return base

    max_suffix = 1
    pattern = re.compile(rf"^{re.escape(base)}-(\d+)$")
    for existing_id in existing_ids:
        match = pattern.match(existing_id)
        if match:
            max_suffix = max(max_suffix, int(match.group(1)))
    return f"{base}-{max_suffix + 1}"


def row_to_entry(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        lastModifiedBy=row["last_modified_by"],
        lastModifiedAt=row["last_modified_at"],
    )


def page_template(title: str, body: str) -> str:
    escaped_title = html.escape(title)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>{escaped_title}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 960px;
            margin: 2rem auto;
            padding: 0 1rem;
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
        .entry-list li {{
            margin: 0.5rem 0;
        }}
        pre {{
            white-space: pre-wrap;
            word-break: break-word;
            background: #f6f8fa;
            padding: 1rem;
            border-radius: 6px;
        }}
        table.diff {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
            margin-top: 1rem;
            table-layout: fixed;
        }}
        .diff_header {{
            background: #f0f0f0;
        }}
        td, th {{
            padding: 0.4rem;
            vertical-align: top;
            word-break: break-word;
        }}
        .diff_add {{
            background-color: #d4fcbc;
        }}
        .diff_chg {{
            background-color: #fff3a3;
        }}
        .diff_sub {{
            background-color: #ffdddd;
        }}
        .edit-block {{
            border: 1px solid #ddd;
            border-radius: 6px;
            padding: 1rem;
            margin: 1rem 0 2rem 0;
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


def get_entry_or_404(conn: sqlite3.Connection, entry_id: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT id, title, content, last_modified_by, last_modified_at
        FROM entries
        WHERE id = ?
        """,
        (entry_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    return row


def client_prefers_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    if not accept:
        return False
    return "text/html" in accept.lower()


@contextmanager
def write_guard() -> Generator[None, None, None]:
    acquired = _write_semaphore.acquire(blocking=False)
    if not acquired:
        raise HTTPException(status_code=429, detail="Too many concurrent write requests")
    try:
        yield
    finally:
        _write_semaphore.release()


@app.get("/entries", response_class=HTMLResponse)
def list_entries() -> HTMLResponse:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, title
            FROM entries
            ORDER BY title COLLATE NOCASE ASC
            """
        ).fetchall()

    items = []
    for row in rows:
        title = html.escape(row["title"])
        entry_id = html.escape(row["id"])
        items.append(f'<li><a href="/entries/{entry_id}">{title}</a></li>')

    body = f"""
    <h1>Wiki Entries</h1>
    <p>List of all entries with links to their pages.</p>
    <ul class="entry-list">
        {''.join(items) if items else '<li>No entries yet.</li>'}
    </ul>
    """
    return HTMLResponse(content=page_template("Wiki Entries", body), status_code=200)


@app.post("/entries", response_model=Entry, status_code=201)
def create_entry(payload: NewEntry) -> Entry:
    now = utc_now_iso()
    with write_guard():
        with get_db() as conn:
            entry_id = unique_entry_id(conn, payload.title)
            conn.execute(
                """
                INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (entry_id, payload.title.strip(), payload.content, payload.createdBy.strip(), now),
            )
            conn.execute(
                """
                INSERT INTO edits (entry_id, content, modified_by, modified_at, summary)
                VALUES (?, ?, ?, ?, ?)
                """,
                (entry_id, payload.content, payload.createdBy.strip(), now, "Initial creation"),
            )
            row = conn.execute(
                """
                SELECT id, title, content, last_modified_by, last_modified_at
                FROM entries
                WHERE id = ?
                """,
                (entry_id,),
            ).fetchone()

    return row_to_entry(row)


@app.get("/entries/{entryId}", response_class=HTMLResponse)
def get_entry(entryId: str) -> HTMLResponse:
    with get_db() as conn:
        row = get_entry_or_404(conn, entryId)
        contributors = conn.execute(
            """
            SELECT DISTINCT modified_by
            FROM edits
            WHERE entry_id = ?
            ORDER BY modified_by COLLATE NOCASE ASC
            """,
            (entryId,),
        ).fetchall()

    title = html.escape(row["title"])
    content = html.escape(row["content"])
    modified_by = html.escape(row["last_modified_by"])
    modified_at = html.escape(row["last_modified_at"])
    contributor_list = ", ".join(html.escape(r["modified_by"]) for r in contributors) or "None"

    body = f"""
    <h1>{title}</h1>
    <p class="meta">Last edited by <strong>{modified_by}</strong> at <strong>{modified_at}</strong></p>
    <p class="meta">Contributors: {contributor_list}</p>
    <h2>Content</h2>
    <pre>{content}</pre>
    <p><a href="/entries/{html.escape(entryId)}/edits">View edit history</a></p>
    """
    return HTMLResponse(content=page_template(row["title"], body), status_code=200)


@app.put("/entries/{entryId}", response_model=Entry)
def update_entry(entryId: str, payload: UpdateEntry) -> Entry:
    now = utc_now_iso()
    with write_guard():
        with get_db() as conn:
            get_entry_or_404(conn, entryId)
            conn.execute(
                """
                UPDATE entries
                SET content = ?, last_modified_by = ?, last_modified_at = ?
                WHERE id = ?
                """,
                (payload.content, payload.modifiedBy.strip(), now, entryId),
            )
            conn.execute(
                """
                INSERT INTO edits (entry_id, content, modified_by, modified_at, summary)
                VALUES (?, ?, ?, ?, ?)
                """,
                (entryId, payload.content, payload.modifiedBy.strip(), now, payload.summary.strip()),
            )
            row = conn.execute(
                """
                SELECT id, title, content, last_modified_by, last_modified_at
                FROM entries
                WHERE id = ?
                """,
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
            LIMIT ?
            """,
            (entryId, MAX_EDITS_RENDERED + 1),
        ).fetchall()

    truncated = len(edits) > MAX_EDITS_RENDERED
    if truncated:
        edits = edits[:MAX_EDITS_RENDERED]

    total_chars = sum(len(edit["content"]) + len(edit["summary"]) + len(edit["modified_by"]) for edit in edits)
    blocks: List[str] = []

    if total_chars > MAX_DIFF_TOTAL_CHARS:
        for index, edit in enumerate(edits, start=1):
            blocks.append(
                f"""
                <div class="edit-block">
                    <h2>Edit {index}</h2>
                    <p class="meta">
                        By <strong>{html.escape(edit["modified_by"])}</strong>
                        at <strong>{html.escape(edit["modified_at"])}</strong>
                    </p>
                    <p><strong>Summary:</strong> {html.escape(edit["summary"])}</p>
                    <p class="meta">Diff omitted because the edit history is too large to render safely.</p>
                </div>
                """
            )
    else:
        diff_tool = HtmlDiff(wrapcolumn=80)
        previous_content = ""
        for index, edit in enumerate(edits, start=1):
            current_content = edit["content"]
            diff_html = diff_tool.make_table(
                previous_content.splitlines(),
                current_content.splitlines(),
                fromdesc="Previous",
                todesc=f"Edit {index}",
                context=True,
                numlines=2,
            )
            blocks.append(
                f"""
                <div class="edit-block">
                    <h2>Edit {index}</h2>
                    <p class="meta">
                        By <strong>{html.escape(edit["modified_by"])}</strong>
                        at <strong>{html.escape(edit["modified_at"])}</strong>
                    </p>
                    <p><strong>Summary:</strong> {html.escape(edit["summary"])}</p>
                    {diff_html}
                </div>
                """
            )
            previous_content = current_content

    notice = ""
    if truncated:
        notice = (
            f'<p class="meta">Showing the first {MAX_EDITS_RENDERED} edits only to protect service availability.</p>'
        )

    body = f"""
    <h1>Edit History: {html.escape(entry["title"])}</h1>
    <p><a href="/entries/{html.escape(entryId)}">Back to entry</a></p>
    {notice}
    {''.join(blocks) if blocks else '<p>No edits found.</p>'}
    """
    return HTMLResponse(content=page_template(f"Edit History - {entry['title']}", body), status_code=200)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 404 and client_prefers_html(request):
        body = """
        <h1>404 - Not Found</h1>
        <p>The requested entry was not found.</p>
        """
        return HTMLResponse(content=page_template("Not Found", body), status_code=404)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)