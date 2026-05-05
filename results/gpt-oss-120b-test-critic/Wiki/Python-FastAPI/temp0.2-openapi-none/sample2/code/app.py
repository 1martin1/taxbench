import uuid
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import difflib
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, constr, validator
from jinja2 import Environment, select_autoescape

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "db.sqlite3"

# Ensure the directory exists (it does, but be explicit)
BASE_DIR.mkdir(parents=True, exist_ok=True)

# Jinja2 environment with auto‑escaping enabled for HTML
jinja_env = Environment(autoescape=select_autoescape(enabled_extensions=("html", "htm", "xml")))

# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #

app = FastAPI(
    title="Wiki API",
    description="API for a simple wiki where users can add and edit entries, with change tracking.",
    version="1.0.0",
)

# Simple CORS (optional, but harmless)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------- #
# Database utilities
# --------------------------------------------------------------------------- #


def get_db():
    """Create a new SQLite connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they do not exist."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
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
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS edits (
                id TEXT PRIMARY KEY,
                entry_id TEXT NOT NULL,
                content TEXT NOT NULL,
                modified_by TEXT NOT NULL,
                summary TEXT,
                edited_at TEXT NOT NULL,
                FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


# Use FastAPI lifespan to run init_db once at startup
@app.on_event("startup")
def _startup():
    init_db()


# --------------------------------------------------------------------------- #
# Pydantic models with validation
# --------------------------------------------------------------------------- #


class NewEntry(BaseModel):
    title: constr(min_length=1, max_length=200)
    content: constr(min_length=1, max_length=5000)
    createdBy: constr(min_length=1, max_length=100) = Field(..., alias="createdBy")

    class Config:
        allow_population_by_field_name = True


class UpdateEntry(BaseModel):
    content: constr(min_length=1, max_length=5000)
    modifiedBy: constr(min_length=1, max_length=100) = Field(..., alias="modifiedBy")
    summary: Optional[constr(max_length=300)] = None

    class Config:
        allow_population_by_field_name = True


class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str = Field(..., alias="lastModifiedBy")
    lastModifiedAt: datetime = Field(..., alias="lastModifiedAt")

    class Config:
        allow_population_by_field_name = True


# --------------------------------------------------------------------------- #
# Templates (auto‑escaped)
# --------------------------------------------------------------------------- #

LIST_TEMPLATE = jinja_env.from_string(
    """
<!DOCTYPE html>
<html>
<head><title>Wiki Entries</title></head>
<body>
    <h1>Wiki Entries</h1>
    <ul>
    {% for entry in entries %}
        <li><a href="/entries/{{ entry.id }}">{{ entry.title }}</a></li>
    {% endfor %}
    </ul>
    <h2>Create New Entry</h2>
    <form method="post" action="/entries">
        <label>Title: <input type="text" name="title" required maxlength="200"></label><br>
        <label>Content:<br><textarea name="content" rows="10" cols="50" required maxlength="5000"></textarea></label><br>
        <label>Created By: <input type="text" name="createdBy" required maxlength="100"></label><br>
        <button type="submit">Create</button>
    </form>
</body>
</html>
"""
)

ENTRY_TEMPLATE = jinja_env.from_string(
    """
<!DOCTYPE html>
<html>
<head><title>{{ entry.title }}</title></head>
<body>
    <h1>{{ entry.title }}</h1>
    <p><em>Last modified by {{ entry.last_modified_by }} at {{ entry.last_modified_at }}</em></p>
    <div style="white-space: pre-wrap; border:1px solid #ccc; padding:10px;">{{ entry.content }}</div>
    <h2>Edit Entry</h2>
    <form method="post" action="/entries/{{ entry.id }}">
        <input type="hidden" name="_method" value="PUT">
        <label>Content:<br><textarea name="content" rows="10" cols="50" required maxlength="5000">{{ entry.content }}</textarea></label><br>
        <label>Modified By: <input type="text" name="modifiedBy" required maxlength="100"></label><br>
        <label>Summary: <input type="text" name="summary" maxlength="300"></label><br>
        <button type="submit">Update</button>
    </form>
    <p><a href="/entries/{{ entry.id }}/edits">View Edit History</a></p>
    <p><a href="/entries">Back to list</a></p>
</body>
</html>
"""
)

EDITS_TEMPLATE = jinja_env.from_string(
    """
<!DOCTYPE html>
<html>
<head><title>Edit History for {{ entry.title }}</title></head>
<body>
    <h1>Edit History for "{{ entry.title }}"</h1>
    {% if edits %}
        <ul>
        {% for edit in edits %}
            <li>
                <strong>{{ edit.edited_at }} by {{ edit.modified_by }}</strong>
                {% if edit.summary %}<em> - {{ edit.summary }}</em>{% endif %}
                <pre style="background:#f8f8f8; padding:10px; border:1px solid #ddd;">{{ edit.diff }}</pre>
            </li>
        {% endfor %}
        </ul>
    {% else %}
        <p>No edits found.</p>
    {% endif %}
    <p><a href="/entries/{{ entry.id }}">Back to entry</a></p>
    <p><a href="/entries">Back to list</a></p>
</body>
</html>
"""
)

# --------------------------------------------------------------------------- #
# Helper functions (DB operations)
# --------------------------------------------------------------------------- #


def fetch_entry(entry_id: str) -> Optional[sqlite3.Row]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
        return cur.fetchone()


def fetch_all_entries() -> List[sqlite3.Row]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, title FROM entries ORDER BY title")
        return cur.fetchall()


def fetch_edits(entry_id: str) -> List[sqlite3.Row]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM edits WHERE entry_id = ? ORDER BY edited_at ASC", (entry_id,)
        )
        return cur.fetchall()


def insert_edit(entry_id: str, content: str, modified_by: str, summary: Optional[str]) -> None:
    edit_id = str(uuid.uuid4())
    edited_at = datetime.utcnow().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO edits (id, entry_id, content, modified_by, summary, edited_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (edit_id, entry_id, content, modified_by, summary, edited_at),
        )
        conn.commit()


def update_entry_db(entry_id: str, title: str, content: str, modified_by: str) -> None:
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE entries
            SET title = ?, content = ?, last_modified_by = ?, last_modified_at = ?
            WHERE id = ?
            """,
            (title, content, modified_by, now, entry_id),
        )
        conn.commit()


# --------------------------------------------------------------------------- #
# Middleware: proper method override using hidden form field
# --------------------------------------------------------------------------- #


@app.middleware("http")
async def method_override(request: Request, call_next):
    if request.method == "POST":
        # Only attempt to read form data if content type is form data
        if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded") or request.headers.get(
            "content-type", ""
        ).startswith("multipart/form-data"):
            form = await request.form()
            override = form.get("_method")
            if override and isinstance(override, str):
                request.scope["method"] = override.upper()
    response = await call_next(request)
    return response


# --------------------------------------------------------------------------- #
# Custom error handling – hide internal details
# --------------------------------------------------------------------------- #


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Return a generic JSON error without exposing internal details
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail if exc.detail else "An error occurred."},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": "Invalid request data."},
    )


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@app.get("/entries", response_class=HTMLResponse)
def list_entries():
    entries = fetch_all_entries()
    html = LIST_TEMPLATE.render(entries=entries)
    return HTMLResponse(content=html)


@app.post("/entries", response_model=Entry, status_code=status.HTTP_201_CREATED)
def create_entry(new_entry: NewEntry):
    entry_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (entry_id, new_entry.title, new_entry.content, new_entry.createdBy, now),
        )
        conn.commit()
    # Record initial edit
    insert_edit(entry_id, new_entry.content, new_entry.createdBy, summary="Created")
    entry = Entry(
        id=entry_id,
        title=new_entry.title,
        content=new_entry.content,
        lastModifiedBy=new_entry.createdBy,
        lastModifiedAt=datetime.fromisoformat(now),
    )
    return entry


@app.get("/entries/{entry_id}", response_class=HTMLResponse)
def get_entry(entry_id: str):
    row = fetch_entry(entry_id)
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    html = ENTRY_TEMPLATE.render(entry=row)
    return HTMLResponse(content=html)


@app.put("/entries/{entry_id}", response_model=Entry)
def update_entry_endpoint(entry_id: str, update: UpdateEntry):
    row = fetch_entry(entry_id)
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    # Update entry (title cannot be changed per spec)
    update_entry_db(entry_id, row["title"], update.content, update.modifiedBy)
    # Insert edit record
    insert_edit(entry_id, update.content, update.modifiedBy, update.summary)
    # Return updated entry
    updated_row = fetch_entry(entry_id)
    entry = Entry(
        id=updated_row["id"],
        title=updated_row["title"],
        content=updated_row["content"],
        lastModifiedBy=updated_row["last_modified_by"],
        lastModifiedAt=datetime.fromisoformat(updated_row["last_modified_at"]),
    )
    return entry


@app.get("/entries/{entry_id}/edits", response_class=HTMLResponse)
def get_edits(entry_id: str):
    entry_row = fetch_entry(entry_id)
    if not entry_row:
        raise HTTPException(status_code=404, detail="Entry not found")
    edits = fetch_edits(entry_id)
    diff_list = []
    previous_content = ""
    for edit in edits:
        current_content = edit["content"]
        diff = "\n".join(
            difflib.unified_diff(
                previous_content.splitlines(),
                current_content.splitlines(),
                fromfile="previous",
                tofile="current",
                lineterm="",
            )
        )
        diff_list.append(
            {
                "edited_at": edit["edited_at"],
                "modified_by": edit["modified_by"],
                "summary": edit["summary"] or "",
                "diff": diff or "(no changes)",
            }
        )
        previous_content = current_content
    html = EDITS_TEMPLATE.render(entry=entry_row, edits=diff_list)
    return HTMLResponse(content=html)