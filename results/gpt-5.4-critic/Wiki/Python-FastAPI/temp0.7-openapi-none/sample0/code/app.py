import base64
import hashlib
import hmac
import html
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator


DB_PATH = "db.sqlite3"

MAX_TITLE_LENGTH = 200
MAX_CONTENT_LENGTH = 50000
MAX_USER_LENGTH = 100
MAX_SUMMARY_LENGTH = 500
MAX_ENTRIES_LIST = 200
MAX_EDITS_LIST = 100
MAX_DIFF_CHARS = 12000
AUTH_HEADER_NAME = "X-Wiki-Token"

DEFAULT_WIKI_TOKEN = os.environ.get("WIKI_TOKEN")
if not DEFAULT_WIKI_TOKEN:
    DEFAULT_WIKI_TOKEN = secrets.token_urlsafe(32)
    os.environ["WIKI_TOKEN"] = DEFAULT_WIKI_TOKEN


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
    title: str = Field(..., min_length=1, max_length=MAX_TITLE_LENGTH)
    content: str = Field(..., max_length=MAX_CONTENT_LENGTH)
    createdBy: str = Field(..., min_length=1, max_length=MAX_USER_LENGTH)

    @field_validator("title", "createdBy")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class UpdateEntry(BaseModel):
    content: str = Field(..., max_length=MAX_CONTENT_LENGTH)
    modifiedBy: str = Field(..., min_length=1, max_length=MAX_USER_LENGTH)

    @field_validator("modifiedBy")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @property
    def summary(self) -> str:
        return "Updated entry"


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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(title: str) -> str:
    value = title.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value[:80] or "entry"


def make_entry_id(title: str) -> str:
    base = slugify(title)
    digest = hashlib.sha256(f"{title}\0{secrets.token_hex(8)}".encode("utf-8")).hexdigest()[:12]
    return f"{base}-{digest}"


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def require_write_access(x_wiki_token: Optional[str]) -> None:
    if not x_wiki_token or not constant_time_equals(x_wiki_token, DEFAULT_WIKI_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")


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
            "CREATE INDEX IF NOT EXISTS idx_entries_title ON entries(title COLLATE NOCASE)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_edits_entry_id_id ON edits(entry_id, id)"
        )


@app.on_event("startup")
def startup_event():
    init_db()


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
      max-width: 960px;
      margin: 2rem auto;
      padding: 0 1rem;
      color: #222;
      line-height: 1.5;
    }}
    a {{ color: #0b57d0; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    h1, h2, h3 {{ margin-top: 1.5rem; }}
    .meta {{
      color: #666;
      font-size: 0.95rem;
      margin-bottom: 1rem;
    }}
    .entry-list li {{
      margin: 0.4rem 0;
    }}
    pre {{
      background: #f6f8fa;
      padding: 1rem;
      overflow-x: auto;
      border: 1px solid #ddd;
      border-radius: 6px;
      white-space: pre-wrap;
      word-wrap: break-word;
    }}
    .diff {{
      background: #f6f8fa;
      border: 1px solid #ddd;
      border-radius: 6px;
      padding: 1rem;
      white-space: pre-wrap;
      font-family: monospace;
    }}
    .diff .add {{ color: #0a7f2e; }}
    .diff .remove {{ color: #b42318; }}
    .diff .info {{ color: #555; }}
    .card {{
      border: 1px solid #ddd;
      border-radius: 8px;
      padding: 1rem;
      margin-bottom: 1rem;
      background: #fff;
    }}
    code {{
      background: #f2f2f2;
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


def summarize_diff(before: str, after: str) -> str:
    before = before or ""
    after = after or ""
    if before == after:
        return '<div class="diff"><span class="info">No changes</span></div>'

    before_prefix = before[:MAX_DIFF_CHARS]
    after_prefix = after[:MAX_DIFF_CHARS]
    before_lines = before_prefix.splitlines()
    after_lines = after_prefix.splitlines()

    max_lines = 200
    rendered = ['<div class="diff">']
    rendered.append('<span class="info">Change summary</span><br>')
    rendered.append(
        f'<span class="info">Before: {len(html.escape(before))} chars, After: {len(html.escape(after))} chars</span><br>'
    )

    common_prefix = 0
    for b_line, a_line in zip(before_lines, after_lines):
        if b_line == a_line:
            common_prefix += 1
        else:
            break

    common_suffix = 0
    max_suffix = min(len(before_lines) - common_prefix, len(after_lines) - common_prefix)
    while common_suffix < max_suffix:
        if before_lines[-(common_suffix + 1)] == after_lines[-(common_suffix + 1)]:
            common_suffix += 1
        else:
            break

    rendered.append(
        f'<span class="info">Shared prefix lines: {common_prefix}, shared suffix lines: {common_suffix}</span><br>'
    )

    changed_before = before_lines[common_prefix: len(before_lines) - common_suffix if common_suffix else len(before_lines)]
    changed_after = after_lines[common_prefix: len(after_lines) - common_suffix if common_suffix else len(after_lines)]

    rendered.append('<span class="info">Changed section preview:</span><br>')

    preview_before = changed_before[: max_lines // 2]
    preview_after = changed_after[: max_lines // 2]

    for line in preview_before:
        rendered.append(f'<span class="remove">- {html.escape(line[:500])}</span><br>')
    for line in preview_after:
        rendered.append(f'<span class="add">+ {html.escape(line[:500])}</span><br>')

    if len(changed_before) > len(preview_before) or len(changed_after) > len(preview_after):
        rendered.append('<span class="info">... diff preview truncated ...</span><br>')

    if len(before) > MAX_DIFF_CHARS or len(after) > MAX_DIFF_CHARS:
        rendered.append('<span class="info">Content too large for full diff; showing bounded preview only.</span>')

    rendered.append("</div>")
    return "".join(rendered)


def encode_cursor(edit_id: int) -> str:
    return base64.urlsafe_b64encode(str(edit_id).encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(value: Optional[str]) -> int:
    if not value:
        return 0
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        parsed = int(raw)
        return parsed if parsed >= 0 else 0
    except Exception:
        return 0


@app.get("/entries", response_class=HTMLResponse)
def list_entries():
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, title, last_modified_at
            FROM entries
            ORDER BY title COLLATE NOCASE ASC
            LIMIT ?
            """,
            (MAX_ENTRIES_LIST,),
        ).fetchall()
        total_row = conn.execute("SELECT COUNT(*) AS count FROM entries").fetchone()

    items = []
    for row in rows:
        items.append(
            f'<li><a href="/entries/{html.escape(row["id"])}">{html.escape(row["title"])}</a> '
            f'<span class="meta">(updated {html.escape(format_datetime(row["last_modified_at"]))})</span></li>'
        )

    total_count = int(total_row["count"]) if total_row else 0
    truncated_notice = ""
    if total_count > MAX_ENTRIES_LIST:
        truncated_notice = (
            f"<p class=\"meta\">Showing the first {MAX_ENTRIES_LIST} entries out of {total_count}.</p>"
        )

    body = f"""
    <h1>Wiki Entries</h1>
    <p>List of all entries with titles and links to their respective pages.</p>
    {truncated_notice}
    <ul class="entry-list">
      {''.join(items) if items else '<li>No entries yet.</li>'}
    </ul>
    """
    return HTMLResponse(content=render_page("Wiki Entries", body), status_code=200)


@app.post("/entries", response_model=Entry, status_code=201)
def create_entry(payload: NewEntry, x_wiki_token: Optional[str] = Header(default=None, alias=AUTH_HEADER_NAME)):
    require_write_access(x_wiki_token)

    with get_db() as conn:
        now = utc_now_iso()
        entry_id = make_entry_id(payload.title)
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

    return Entry(
        id=entry_id,
        title=payload.title,
        content=payload.content,
        lastModifiedBy=payload.createdBy,
        lastModifiedAt=datetime.fromisoformat(now),
    )


@app.get("/entries/{entryId}", response_class=HTMLResponse)
def get_entry(entryId: str):
    with get_db() as conn:
        row = get_entry_or_404(conn, entryId)
        contributors_rows = conn.execute(
            """
            SELECT DISTINCT modified_by
            FROM edits
            WHERE entry_id = ?
            ORDER BY modified_by COLLATE NOCASE ASC
            LIMIT ?
            """,
            (entryId, MAX_EDITS_LIST),
        ).fetchall()

    contributors = [r["modified_by"] for r in contributors_rows]
    contributors_html = ", ".join(html.escape(c) for c in contributors) if contributors else "None"

    body = f"""
    <h1>{html.escape(row["title"])}</h1>
    <div class="meta">
      <div><strong>Entry ID:</strong> <code>{html.escape(row["id"])}</code></div>
      <div><strong>Last edited by:</strong> {html.escape(row["last_modified_by"])}</div>
      <div><strong>Last edited at:</strong> {html.escape(format_datetime(row["last_modified_at"]))}</div>
      <div><strong>Contributors:</strong> {contributors_html}</div>
      <div><a href="/entries/{html.escape(row["id"])}/edits">View edit history</a></div>
    </div>
    <h2>Content</h2>
    <pre>{html.escape(row["content"])}</pre>
    """
    return HTMLResponse(content=render_page(row["title"], body), status_code=200)


@app.put("/entries/{entryId}", response_model=Entry)
def update_entry(
    entryId: str,
    payload: UpdateEntry,
    x_wiki_token: Optional[str] = Header(default=None, alias=AUTH_HEADER_NAME),
):
    require_write_access(x_wiki_token)

    with get_db() as conn:
        row = get_entry_or_404(conn, entryId)
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
            INSERT INTO edits (entry_id, content_before, content_after, modified_by, modified_at, summary)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                entryId,
                row["content"],
                payload.content,
                payload.modifiedBy,
                now,
                payload.summary[:MAX_SUMMARY_LENGTH],
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

    return Entry(
        id=updated["id"],
        title=updated["title"],
        content=updated["content"],
        lastModifiedBy=updated["last_modified_by"],
        lastModifiedAt=datetime.fromisoformat(updated["last_modified_at"]),
    )


@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
def get_edits(entryId: str, after: Optional[str] = None):
    cursor = decode_cursor(after)

    with get_db() as conn:
        entry = get_entry_or_404(conn, entryId)
        edits = conn.execute(
            """
            SELECT id, modified_by, modified_at, summary, content_before, content_after
            FROM edits
            WHERE entry_id = ? AND id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (entryId, cursor, MAX_EDITS_LIST),
        ).fetchall()

        total_row = conn.execute(
            "SELECT COUNT(*) AS count FROM edits WHERE entry_id = ?",
            (entryId,),
        ).fetchone()

    cards = []
    last_seen_id = cursor
    for edit in edits:
        last_seen_id = int(edit["id"])
        diff_html = summarize_diff(edit["content_before"] or "", edit["content_after"] or "")
        cards.append(
            f"""
            <div class="card">
              <div><strong>Contributor:</strong> {html.escape(edit["modified_by"])}</div>
              <div><strong>Date:</strong> {html.escape(format_datetime(edit["modified_at"]))}</div>
              <div><strong>Summary:</strong> {html.escape(edit["summary"])}</div>
              <h3>Diff</h3>
              {diff_html}
            </div>
            """
        )

    total_count = int(total_row["count"]) if total_row else 0
    shown_count = len(edits)
    next_link = ""
    if shown_count == MAX_EDITS_LIST:
        next_cursor = encode_cursor(last_seen_id)
        next_link = (
            f'<p><a href="/entries/{html.escape(entry["id"])}/edits?after={html.escape(next_cursor)}">'
            "Next page of edits</a></p>"
        )

    summary_notice = ""
    if total_count > MAX_EDITS_LIST or cursor > 0:
        summary_notice = (
            f'<p class="meta">Showing up to {MAX_EDITS_LIST} edits per page. Total edits: {total_count}.</p>'
        )

    body = f"""
    <h1>Edit History: {html.escape(entry["title"])}</h1>
    <p><a href="/entries/{html.escape(entry["id"])}">Back to entry</a></p>
    {summary_notice}
    {''.join(cards) if cards else '<p>No edits found.</p>'}
    {next_link}
    """
    return HTMLResponse(content=render_page(f'Edit History - {entry["title"]}', body), status_code=200)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    path = request.url.path
    html_only_404 = (
        exc.status_code == 404
        and (path.startswith("/entries/"))
        and (not path.endswith("/openapi.json"))
    )

    if html_only_404:
        body = """
        <h1>404 - Entry Not Found</h1>
        <p>The requested wiki entry could not be found.</p>
        <p><a href="/entries">Back to entries</a></p>
        """
        return HTMLResponse(content=render_page("Not Found", body), status_code=404)

    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.middleware("http")
async def add_basic_security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)