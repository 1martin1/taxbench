import html
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from difflib import HtmlDiff
from threading import Lock

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator


DB_PATH = "db.sqlite3"

MAX_TITLE_LENGTH = 200
MAX_USER_LENGTH = 100
MAX_SUMMARY_LENGTH = 500
MAX_CONTENT_LENGTH = 100_000
MAX_REQUEST_BODY_BYTES = 120_000

MAX_ENTRIES_LIST = 200
MAX_EDITS_HISTORY = 50
MAX_DIFF_CONTENT_CHARS = 20_000

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 120
RATE_LIMIT_MAX_WRITES = 30
RATE_LIMIT_MAX_EXPENSIVE = 20

_rate_lock = Lock()
_request_log: dict[str, deque[float]] = defaultdict(deque)
_write_log: dict[str, deque[float]] = defaultdict(deque)
_expensive_log: dict[str, deque[float]] = defaultdict(deque)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_db()
    try:
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
                content_before TEXT,
                content_after TEXT NOT NULL,
                modified_by TEXT NOT NULL,
                modified_at TEXT NOT NULL,
                summary TEXT NOT NULL,
                FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_edits_entry_id ON edits(entry_id);
            CREATE INDEX IF NOT EXISTS idx_entries_title ON entries(title);
            """
        )
        conn.commit()
    finally:
        conn.close()


def normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _prune(queue: deque[float], now: float) -> None:
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    while queue and queue[0] < cutoff:
        queue.popleft()


def enforce_rate_limit(bucket: dict[str, deque[float]], key: str, limit: int) -> None:
    now = time.time()
    with _rate_lock:
        queue = bucket[key]
        _prune(queue, now)
        if len(queue) >= limit:
            raise HTTPException(status_code=429, detail="Too many requests")
        queue.append(now)


def html_error_page(status_code: int, detail: str) -> str:
    body = f"""
    <h1>Error {status_code}</h1>
    <div class="card">{html.escape(detail)}</div>
    """
    return page_template(f"Error {status_code}", body)


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

    @field_validator("title", "createdBy")
    @classmethod
    def validate_trimmed_required(cls, value: str) -> str:
        value = normalize_text(value)
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n")


class UpdateEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., max_length=MAX_CONTENT_LENGTH)
    modifiedBy: str = Field(..., min_length=1, max_length=MAX_USER_LENGTH)
    summary: str = Field(..., min_length=1, max_length=MAX_SUMMARY_LENGTH)

    @field_validator("modifiedBy", "summary")
    @classmethod
    def validate_trimmed_required(cls, value: str) -> str:
        value = normalize_text(value)
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n")


app = FastAPI(
    title="Wiki API",
    description="API for a simple wiki where users can add and edit entries, with change tracking.",
    version="1.0.0",
)


def entry_row_to_model(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        lastModifiedBy=row["last_modified_by"],
        lastModifiedAt=row["last_modified_at"],
    )


def page_template(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      margin: 2rem auto;
      max-width: 960px;
      padding: 0 1rem;
      line-height: 1.5;
      color: #222;
      background: #fafafa;
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
      margin-bottom: 1rem;
    }}
    .card {{
      background: white;
      border: 1px solid #ddd;
      border-radius: 8px;
      padding: 1rem 1.25rem;
      margin-bottom: 1rem;
      box-shadow: 0 1px 2px rgba(0,0,0,0.04);
    }}
    .content {{
      white-space: pre-wrap;
      background: white;
      border: 1px solid #ddd;
      border-radius: 8px;
      padding: 1rem;
    }}
    ul {{
      padding-left: 1.2rem;
    }}
    table.diff {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.9rem;
      background: white;
      table-layout: fixed;
      word-wrap: break-word;
    }}
    .diff_header {{
      background: #f0f0f0;
    }}
    td, th {{
      padding: 0.35rem 0.5rem;
      border: 1px solid #ddd;
      vertical-align: top;
    }}
    .diff_add {{
      background: #e6ffed;
    }}
    .diff_chg {{
      background: #fff5b1;
    }}
    .diff_sub {{
      background: #ffeef0;
    }}
    .nav {{
      margin-bottom: 1.5rem;
    }}
    .nav a {{
      margin-right: 1rem;
    }}
    .small {{
      font-size: 0.9rem;
      color: #666;
    }}
  </style>
</head>
<body>
  <div class="nav">
    <a href="/entries">All Entries</a>
  </div>
  {body}
</body>
</html>"""


def wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    if not accept or "*/*" in accept:
        return True
    return "text/html" in accept.lower()


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    ip = client_ip(request)
    enforce_rate_limit(_request_log, ip, RATE_LIMIT_MAX_REQUESTS)

    if request.method in {"POST", "PUT", "PATCH"}:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="Request body too large")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid Content-Length")
        enforce_rate_limit(_write_log, ip, RATE_LIMIT_MAX_WRITES)

    if request.method == "GET" and request.url.path.endswith("/edits"):
        enforce_rate_limit(_expensive_log, ip, RATE_LIMIT_MAX_EXPENSIVE)

    response = await call_next(request)
    return response


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/entries", response_class=HTMLResponse)
def list_entries() -> HTMLResponse:
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, title, last_modified_by, last_modified_at
            FROM entries
            ORDER BY title COLLATE NOCASE ASC
            LIMIT ?
            """,
            (MAX_ENTRIES_LIST,),
        ).fetchall()

        total_row = conn.execute("SELECT COUNT(*) AS count FROM entries").fetchone()
        total_count = int(total_row["count"]) if total_row is not None else 0
    finally:
        conn.close()

    items = []
    for row in rows:
        entry_url = f"/entries/{row['id']}"
        edits_url = f"/entries/{row['id']}/edits"
        items.append(
            f"""
            <li class="card">
              <h2><a href="{html.escape(entry_url)}">{html.escape(row["title"])}</a></h2>
              <div class="meta">
                Last modified by {html.escape(row["last_modified_by"])} at {html.escape(row["last_modified_at"])}
              </div>
              <div><a href="{html.escape(entry_url)}">View entry</a> · <a href="{html.escape(edits_url)}">View edit history</a></div>
            </li>
            """
        )

    notice = ""
    if total_count > MAX_ENTRIES_LIST:
        notice = (
            f'<p class="small">Showing the first {MAX_ENTRIES_LIST} entries out of {total_count} total.</p>'
        )

    body = f"""
    <h1>Wiki Entries</h1>
    <p class="small">Create entries with POST /entries and update them with PUT /entries/{{entryId}}.</p>
    {notice}
    <ul>
      {''.join(items) if items else '<li class="card">No entries yet.</li>'}
    </ul>
    """
    return HTMLResponse(content=page_template("Wiki Entries", body), status_code=200)


@app.post("/entries", response_model=Entry, status_code=201)
def create_entry(payload: NewEntry) -> Entry:
    entry_id = str(uuid.uuid4())
    now = utc_now_iso()

    conn = get_db()
    try:
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
            """
            SELECT id, title, content, last_modified_by, last_modified_at
            FROM entries
            WHERE id = ?
            """,
            (entry_id,),
        ).fetchone()
    finally:
        conn.close()

    return entry_row_to_model(row)


@app.get("/entries/{entryId}", response_class=HTMLResponse)
def get_entry(entryId: str) -> HTMLResponse:
    conn = get_db()
    try:
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

        contributor_rows = conn.execute(
            """
            SELECT DISTINCT modified_by
            FROM edits
            WHERE entry_id = ?
            ORDER BY modified_by COLLATE NOCASE ASC
            """,
            (entryId,),
        ).fetchall()
    finally:
        conn.close()

    contributors = [r["modified_by"] for r in contributor_rows]
    contributor_html = ", ".join(html.escape(name) for name in contributors) if contributors else "None"

    body = f"""
    <h1>{html.escape(row["title"])}</h1>
    <div class="meta">
      Last edited by {html.escape(row["last_modified_by"])} at {html.escape(row["last_modified_at"])}
    </div>
    <div class="meta">
      Contributors: {contributor_html}
    </div>
    <p><a href="/entries/{html.escape(entryId)}/edits">View edit history</a></p>
    <div class="content">{html.escape(row["content"])}</div>
    """
    return HTMLResponse(content=page_template(row["title"], body), status_code=200)


@app.put("/entries/{entryId}", response_model=Entry)
def update_entry(entryId: str, payload: UpdateEntry) -> Entry:
    conn = get_db()
    try:
        existing = conn.execute(
            """
            SELECT id, title, content, last_modified_by, last_modified_at
            FROM entries
            WHERE id = ?
            """,
            (entryId,),
        ).fetchone()

        if existing is None:
            raise HTTPException(status_code=404, detail="Entry not found")

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
                existing["content"],
                payload.content,
                payload.modifiedBy,
                now,
                payload.summary,
            ),
        )
        conn.commit()

        updated = conn.execute(
            """
            SELECT id, title, content, last_modified_by, last_modified_at
            FROM entries
            WHERE id = ?
            """,
            (entryId,),
        ).fetchone()
    finally:
        conn.close()

    return entry_row_to_model(updated)


@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
def get_entry_edits(entryId: str) -> HTMLResponse:
    conn = get_db()
    try:
        entry = conn.execute(
            """
            SELECT id, title
            FROM entries
            WHERE id = ?
            """,
            (entryId,),
        ).fetchone()

        if entry is None:
            raise HTTPException(status_code=404, detail="Entry not found")

        edits = conn.execute(
            """
            SELECT id, content_before, content_after, modified_by, modified_at, summary
            FROM edits
            WHERE entry_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (entryId, MAX_EDITS_HISTORY),
        ).fetchall()

        total_row = conn.execute(
            "SELECT COUNT(*) AS count FROM edits WHERE entry_id = ?",
            (entryId,),
        ).fetchone()
        total_count = int(total_row["count"]) if total_row is not None else 0
    finally:
        conn.close()

    diff_maker = HtmlDiff(wrapcolumn=80)
    edit_blocks = []

    for idx, edit in enumerate(reversed(edits), start=1):
        before_text = edit["content_before"] or ""
        after_text = edit["content_after"] or ""

        if len(before_text) > MAX_DIFF_CONTENT_CHARS or len(after_text) > MAX_DIFF_CONTENT_CHARS:
            diff_html = (
                '<div class="card">Diff omitted because the entry content is too large to render safely.</div>'
            )
        else:
            before = before_text.splitlines()
            after = after_text.splitlines()
            diff_html = diff_maker.make_table(
                before,
                after,
                fromdesc="Before",
                todesc="After",
                context=True,
                numlines=2,
            )

        edit_blocks.append(
            f"""
            <div class="card">
              <h2>Edit #{idx}</h2>
              <div class="meta">
                By {html.escape(edit["modified_by"])} at {html.escape(edit["modified_at"])}
              </div>
              <div class="meta">
                Summary: {html.escape(edit["summary"])}
              </div>
              <div>{diff_html}</div>
            </div>
            """
        )

    notice = ""
    if total_count > MAX_EDITS_HISTORY:
        notice = (
            f'<p class="small">Showing the most recent {MAX_EDITS_HISTORY} edits out of {total_count} total.</p>'
        )

    body = f"""
    <h1>Edit History: {html.escape(entry["title"])}</h1>
    <p><a href="/entries/{html.escape(entryId)}">Back to entry</a></p>
    {notice}
    {''.join(edit_blocks) if edit_blocks else '<div class="card">No edits found.</div>'}
    """
    return HTMLResponse(content=page_template(f"Edit History - {entry['title']}", body), status_code=200)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/entries") and request.method == "GET":
        return HTMLResponse(
            content=html_error_page(exc.status_code, str(exc.detail)),
            status_code=exc.status_code,
        )
    if wants_html(request):
        return HTMLResponse(
            content=html_error_page(exc.status_code, str(exc.detail)),
            status_code=exc.status_code,
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if wants_html(request) and request.method == "GET":
        return HTMLResponse(
            content=html_error_page(422, "Validation error"),
            status_code=422,
        )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    if wants_html(request) and request.method == "GET":
        return HTMLResponse(
            content=html_error_page(500, "Internal server error"),
            status_code=500,
        )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/openapi.json", include_in_schema=False)
def custom_openapi() -> Response:
    schema = app.openapi()
    update_entry_schema = (
        schema.get("components", {})
        .get("schemas", {})
        .get("UpdateEntry", {})
    )
    properties = update_entry_schema.setdefault("properties", {})
    properties["summary"] = {"type": "string"}
    required = update_entry_schema.setdefault("required", [])
    if "summary" not in required:
        required.append("summary")
    return JSONResponse(content=schema)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)