import difflib
import html
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Path, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator


DB_PATH = "db.sqlite3"

MAX_TITLE_LENGTH = 200
MAX_CONTENT_LENGTH = 100_000
MAX_ACTOR_LENGTH = 100
MAX_SUMMARY_LENGTH = 500
MAX_LIST_ENTRIES = 500
MAX_EDIT_HISTORY = 100
MAX_DIFF_TEXT_LENGTH = 20_000
MAX_REQUEST_BODY_BYTES = 262_144
AUTH_TOKEN = "wiki-secret-token"


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
    createdBy: str = Field(..., min_length=1, max_length=MAX_ACTOR_LENGTH)

    @field_validator("title", "createdBy")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class UpdateEntry(BaseModel):
    content: str = Field(..., max_length=MAX_CONTENT_LENGTH)
    modifiedBy: str = Field(..., min_length=1, max_length=MAX_ACTOR_LENGTH)
    summary: str = Field(..., min_length=1, max_length=MAX_SUMMARY_LENGTH)

    @field_validator("modifiedBy", "summary")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


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
    slug = title.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "entry"


def ensure_unique_entry_id(conn: sqlite3.Connection, title: str) -> str:
    base = slugify(title)
    rows = conn.execute(
        """
        SELECT id
        FROM entries
        WHERE id = ? OR id GLOB ?
        """,
        (base, f"{base}-*"),
    ).fetchall()
    existing_ids = {row["id"] for row in rows}
    if base not in existing_ids:
        return base

    max_suffix = 1
    prefix = f"{base}-"
    for existing_id in existing_ids:
        if existing_id.startswith(prefix):
            suffix = existing_id[len(prefix):]
            if suffix.isdigit():
                max_suffix = max(max_suffix, int(suffix))
    return f"{base}-{max_suffix + 1}"


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
            "CREATE INDEX IF NOT EXISTS idx_edits_entry_id_id ON edits(entry_id, id DESC)"
        )


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                )
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
    return await call_next(request)


def require_auth(request: Request) -> None:
    auth_header = request.headers.get("authorization", "")
    expected = f"Bearer {AUTH_TOKEN}"
    if auth_header != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def fetch_entry_or_404(conn: sqlite3.Connection, entry_id: str) -> sqlite3.Row:
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
    h1, h2, h3 {{
      margin-top: 1.5rem;
    }}
    .meta {{
      color: #666;
      font-size: 0.95rem;
    }}
    .content {{
      white-space: pre-wrap;
      border: 1px solid #ddd;
      padding: 1rem;
      background: #fafafa;
      border-radius: 6px;
    }}
    .entry-list li {{
      margin: 0.4rem 0;
    }}
    .diff {{
      background: #111;
      color: #eee;
      padding: 1rem;
      border-radius: 6px;
      overflow-x: auto;
      white-space: pre-wrap;
    }}
    .edit {{
      border: 1px solid #ddd;
      border-radius: 6px;
      padding: 1rem;
      margin: 1rem 0;
      background: #fcfcfc;
    }}
    .contributors {{
      margin-top: 0.5rem;
    }}
    .notice {{
      color: #666;
      font-size: 0.9rem;
      margin-top: 1rem;
    }}
  </style>
</head>
<body>
{body}
</body>
</html>"""


def row_to_entry(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        lastModifiedBy=row["last_modified_by"],
        lastModifiedAt=datetime.fromisoformat(row["last_modified_at"]),
    )


@app.get("/entries", response_class=HTMLResponse)
def list_entries() -> HTMLResponse:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, title
            FROM entries
            ORDER BY title COLLATE NOCASE ASC
            LIMIT ?
            """,
            (MAX_LIST_ENTRIES,),
        ).fetchall()
        total_count = conn.execute("SELECT COUNT(*) AS count FROM entries").fetchone()["count"]

    items = []
    for row in rows:
        title = html.escape(row["title"])
        entry_id = html.escape(row["id"])
        items.append(f'<li><a href="/entries/{entry_id}">{title}</a></li>')

    notice = ""
    if total_count > MAX_LIST_ENTRIES:
        notice = (
            f'<p class="notice">Showing the first {MAX_LIST_ENTRIES} entries '
            f"out of {total_count}.</p>"
        )

    body = f"""
    <h1>Wiki Entries</h1>
    <p class="meta">All wiki entries with links to their pages.</p>
    <ul class="entry-list">
      {''.join(items) if items else '<li>No entries found.</li>'}
    </ul>
    {notice}
    """
    return HTMLResponse(render_page("Wiki Entries", body), status_code=200)


@app.post("/entries", response_model=Entry, status_code=201)
def create_entry(request: Request, payload: NewEntry = Body(...)) -> Entry:
    require_auth(request)
    now = utc_now_iso()
    with get_db() as conn:
        entry_id = ensure_unique_entry_id(conn, payload.title)
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
def get_entry(entryId: str = Path(...)) -> HTMLResponse:
    with get_db() as conn:
        row = fetch_entry_or_404(conn, entryId)
        contributors_rows = conn.execute(
            """
            SELECT DISTINCT modified_by
            FROM edits
            WHERE entry_id = ?
            ORDER BY modified_by COLLATE NOCASE ASC
            """,
            (entryId,),
        ).fetchall()

    contributors = [html.escape(r["modified_by"]) for r in contributors_rows]
    contributors_html = ", ".join(contributors) if contributors else "None"

    body = f"""
    <p><a href="/entries">← Back to entries</a></p>
    <h1>{html.escape(row["title"])}</h1>
    <p class="meta">
      Last edited by <strong>{html.escape(row["last_modified_by"])}</strong>
      at <strong>{html.escape(row["last_modified_at"])}</strong>
    </p>
    <p class="contributors"><strong>Contributors:</strong> {contributors_html}</p>
    <p><a href="/entries/{html.escape(entryId)}/edits">View edit history</a></p>
    <div class="content">{html.escape(row["content"])}</div>
    """
    return HTMLResponse(render_page(row["title"], body), status_code=200)


@app.put("/entries/{entryId}", response_model=Entry)
def update_entry(
    request: Request,
    entryId: str = Path(...),
    payload: UpdateEntry = Body(...),
) -> Entry:
    require_auth(request)
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
            INSERT INTO edits (entry_id, content_before, content_after, modified_by, modified_at, summary)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                entryId,
                row["content"],
                payload.content,
                payload.modifiedBy,
                now,
                payload.summary,
            ),
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


def build_diff(before: str, after: str) -> str:
    before_text = before[:MAX_DIFF_TEXT_LENGTH]
    after_text = after[:MAX_DIFF_TEXT_LENGTH]
    diff_lines = difflib.unified_diff(
        before_text.splitlines(),
        after_text.splitlines(),
        fromfile="before",
        tofile="after",
        lineterm="",
    )
    diff_text = "\n".join(diff_lines) or "No textual changes."
    if len(before) > MAX_DIFF_TEXT_LENGTH or len(after) > MAX_DIFF_TEXT_LENGTH:
        diff_text += "\n\n[Diff truncated due to size limits.]"
    return diff_text


@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
def get_entry_edits(entryId: str = Path(...)) -> HTMLResponse:
    with get_db() as conn:
        entry = fetch_entry_or_404(conn, entryId)
        edits = conn.execute(
            """
            SELECT modified_by, modified_at, summary, content_before, content_after
            FROM edits
            WHERE entry_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (entryId, MAX_EDIT_HISTORY),
        ).fetchall()
        total_edits = conn.execute(
            "SELECT COUNT(*) AS count FROM edits WHERE entry_id = ?",
            (entryId,),
        ).fetchone()["count"]

    edit_blocks: List[str] = []
    for edit in edits:
        diff_text = build_diff(edit["content_before"], edit["content_after"])
        edit_blocks.append(
            f"""
            <div class="edit">
              <p class="meta">
                <strong>Contributor:</strong> {html.escape(edit["modified_by"])}<br>
                <strong>When:</strong> {html.escape(edit["modified_at"])}<br>
                <strong>Summary:</strong> {html.escape(edit["summary"])}
              </p>
              <pre class="diff">{html.escape(diff_text)}</pre>
            </div>
            """
        )

    notice = ""
    if total_edits > MAX_EDIT_HISTORY:
        notice = (
            f'<p class="notice">Showing the most recent {MAX_EDIT_HISTORY} edits '
            f"out of {total_edits}.</p>"
        )

    body = f"""
    <p><a href="/entries">← Back to entries</a> | <a href="/entries/{html.escape(entryId)}">Back to entry</a></p>
    <h1>Edit History: {html.escape(entry["title"])}</h1>
    <p class="meta">All edits for this entry, including contributor, summary, and a diff of changes.</p>
    {notice}
    {''.join(edit_blocks) if edit_blocks else '<p>No edits found.</p>'}
    """
    return HTMLResponse(render_page(f"Edit History - {entry['title']}", body), status_code=200)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 404 and request.method == "GET":
        if request.url.path == "/entries" or re.fullmatch(r"/entries/[^/]+", request.url.path) or re.fullmatch(
            r"/entries/[^/]+/edits", request.url.path
        ):
            body = """
            <p><a href="/entries">← Back to entries</a></p>
            <h1>404 - Entry not found</h1>
            <p class="meta">The requested wiki entry does not exist.</p>
            """
            return HTMLResponse(render_page("Entry not found", body), status_code=404)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    update_entry_schema = components.get("UpdateEntry")
    if update_entry_schema is not None:
        properties = update_entry_schema.setdefault("properties", {})
        properties.setdefault("summary", {"type": "string"})
        required = update_entry_schema.setdefault("required", [])
        if "summary" not in required:
            required.append("summary")
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)