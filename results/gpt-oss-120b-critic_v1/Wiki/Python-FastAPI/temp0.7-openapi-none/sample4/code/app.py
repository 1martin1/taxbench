import uuid
import sqlite3
from datetime import datetime
from typing import List, Optional

import difflib
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError, constr, ConfigDict
from jinja2 import Environment, select_autoescape

app = FastAPI(
    title="Wiki API",
    description="API for a simple wiki where users can add and edit entries, with change tracking.",
    version="1.0.0",
)

DB_PATH = "db.sqlite3"
MAX_TITLE_LENGTH = 200
MAX_CONTENT_LENGTH = 5000
MAX_SUMMARY_LENGTH = 500
MAX_EDITS_RETURNED = 200  # safety limit for edit history


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_by TEXT NOT NULL,
            last_modified_by TEXT NOT NULL,
            last_modified_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL,
            content TEXT NOT NULL,
            modified_by TEXT NOT NULL,
            summary TEXT,
            modified_at TEXT NOT NULL,
            FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()


@app.on_event("startup")
def on_startup():
    init_db()


# ------------------- Pydantic models -------------------


class EntryModel(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str = Field(..., alias="last_modified_by")
    lastModifiedAt: datetime = Field(..., alias="last_modified_at")

    model_config = ConfigDict(populate_by_name=True)


class NewEntry(BaseModel):
    title: constr(max_length=MAX_TITLE_LENGTH)
    content: constr(max_length=MAX_CONTENT_LENGTH)
    createdBy: constr(max_length=100) = Field(..., alias="created_by")

    model_config = ConfigDict(populate_by_name=True)


class UpdateEntry(BaseModel):
    content: constr(max_length=MAX_CONTENT_LENGTH)
    modifiedBy: constr(max_length=100) = Field(..., alias="modified_by")
    summary: constr(max_length=MAX_SUMMARY_LENGTH)

    model_config = ConfigDict(populate_by_name=True)


# ------------------- Jinja2 Environment -------------------

jinja_env = Environment(autoescape=select_autoescape(default_for_string=True, default=True))


LIST_TEMPLATE = jinja_env.from_string(
    """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Wiki Entries</title>
    </head>
    <body>
        <h1>Wiki Entries</h1>
        <ul>
        {% for entry in entries %}
            <li><a href="/entries/{{ entry.id }}">{{ entry.title }}</a></li>
        {% endfor %}
        </ul>
        <h2>Create a new entry</h2>
        <form method="post" action="/entries">
            <label>Title: <input type="text" name="title" required maxlength="{{ max_title }}"></label><br>
            <label>Content:<br><textarea name="content" rows="5" cols="40" required maxlength="{{ max_content }}"></textarea></label><br>
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
    <head>
        <title>{{ entry.title }}</title>
    </head>
    <body>
        <h1>{{ entry.title }}</h1>
        <p><em>Last modified by {{ entry.last_modified_by }} at {{ entry.last_modified_at }}</em></p>
        <div style="white-space: pre-wrap; border:1px solid #ccc; padding:10px;">{{ entry.content }}</div>
        <h2>Edit this entry</h2>
        <form method="post" action="/entries/{{ entry.id }}?_method=PUT">
            <label>Content:<br><textarea name="content" rows="5" cols="40" required maxlength="{{ max_content }}">{{ entry.content }}</textarea></label><br>
            <label>Modified By: <input type="text" name="modifiedBy" required maxlength="100"></label><br>
            <label>Summary: <input type="text" name="summary" maxlength="{{ max_summary }}"></label><br>
            <button type="submit">Update</button>
        </form>
        <p><a href="/entries/{{ entry.id }}/edits">View edit history</a></p>
        <p><a href="/entries">Back to list</a></p>
    </body>
    </html>
    """
)

EDITS_TEMPLATE = jinja_env.from_string(
    """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Edit History for {{ entry.title }}</title>
        <style>
            pre {background:#f8f8f8; padding:10px; border:1px solid #ddd;}
        </style>
    </head>
    <body>
        <h1>Edit History for "{{ entry.title }}"</h1>
        {% for edit in edits %}
            <h3>Edit #{{ loop.index }} by {{ edit.modified_by }} at {{ edit.modified_at }}</h3>
            {% if edit.summary %}
                <p><strong>Summary:</strong> {{ edit.summary }}</p>
            {% endif %}
            {% if edit.diff %}
                <pre>{{ edit.diff }}</pre>
            {% else %}
                <p>No previous version to diff against.</p>
            {% endif %}
            <hr>
        {% endfor %}
        <p><a href="/entries/{{ entry.id }}">Back to entry</a></p>
        <p><a href="/entries">Back to list</a></p>
    </body>
    </html>
    """
)


# ------------------- Helper Functions -------------------


def fetch_entry(entry_id: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
    row = cur.fetchone()
    conn.close()
    return row


def fetch_all_entries() -> List[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM entries ORDER BY title")
    rows = cur.fetchall()
    conn.close()
    return rows


def fetch_edits(entry_id: str, limit: int = MAX_EDITS_RETURNED) -> List[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM edits WHERE entry_id = ? ORDER BY modified_at ASC LIMIT ?",
        (entry_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def insert_edit(entry_id: str, content: str, modified_by: str, summary: str):
    conn = get_db()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute(
        """
        INSERT INTO edits (entry_id, content, modified_by, summary, modified_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entry_id, content, modified_by, summary, now),
    )
    conn.commit()
    conn.close()


# ------------------- Routes -------------------


@app.get("/entries", response_class=HTMLResponse)
def list_entries():
    entries = fetch_all_entries()
    rendered = LIST_TEMPLATE.render(
        entries=entries,
        max_title=MAX_TITLE_LENGTH,
        max_content=MAX_CONTENT_LENGTH,
    )
    return HTMLResponse(content=rendered)


@app.post("/entries")
async def create_entry(request: Request):
    """
    Accept both JSON and form submissions.
    """
    if request.headers.get("content-type", "").startswith("application/json"):
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)

    try:
        new_entry = NewEntry(**data)
    except ValidationError:
        raise HTTPException(status_code=400, detail="Invalid request data")

    entry_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO entries (id, title, content, created_by, last_modified_by, last_modified_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            entry_id,
            new_entry.title,
            new_entry.content,
            new_entry.createdBy,
            new_entry.createdBy,
            now,
        ),
    )
    conn.commit()
    conn.close()

    # initial edit record
    insert_edit(entry_id, new_entry.content, new_entry.createdBy, "Created")

    entry_dict = {
        "id": entry_id,
        "title": new_entry.title,
        "content": new_entry.content,
        "last_modified_by": new_entry.createdBy,
        "last_modified_at": now,
    }
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=entry_dict)


@app.get("/entries/{entry_id}", response_class=HTMLResponse)
def get_entry(entry_id: str):
    row = fetch_entry(entry_id)
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    rendered = ENTRY_TEMPLATE.render(
        entry=row,
        max_content=MAX_CONTENT_LENGTH,
        max_summary=MAX_SUMMARY_LENGTH,
    )
    return HTMLResponse(content=rendered)


@app.put("/entries/{entry_id}")
async def update_entry(entry_id: str, request: Request):
    """
    Accept both JSON and form submissions.
    """
    if request.headers.get("content-type", "").startswith("application/json"):
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)

    try:
        upd = UpdateEntry(**data)
    except ValidationError:
        raise HTTPException(status_code=400, detail="Invalid request data")

    entry = fetch_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    now = datetime.utcnow().isoformat()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE entries
        SET content = ?, last_modified_by = ?, last_modified_at = ?
        WHERE id = ?
        """,
        (upd.content, upd.modifiedBy, now, entry_id),
    )
    conn.commit()
    conn.close()

    insert_edit(entry_id, upd.content, upd.modifiedBy, upd.summary)

    updated_entry = {
        "id": entry_id,
        "title": entry["title"],
        "content": upd.content,
        "last_modified_by": upd.modifiedBy,
        "last_modified_at": now,
    }
    return JSONResponse(content=updated_entry)


@app.get("/entries/{entry_id}/edits", response_class=HTMLResponse)
def get_edits(entry_id: str):
    entry = fetch_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    edits_rows = fetch_edits(entry_id)
    edits = []
    previous_content = None
    for row in edits_rows:
        diff_text = ""
        if previous_content is not None:
            diff = difflib.unified_diff(
                previous_content.splitlines(),
                row["content"].splitlines(),
                fromfile="previous",
                tofile="current",
                lineterm="",
            )
            diff_text = "\n".join(diff)
        edits.append(
            {
                "modified_by": row["modified_by"],
                "modified_at": row["modified_at"],
                "summary": row["summary"],
                "diff": diff_text,
            }
        )
        previous_content = row["content"]

    rendered = EDITS_TEMPLATE.render(entry=entry, edits=edits)
    return HTMLResponse(content=rendered)


# ------------------- Middleware for HTML Form method override -------------------


@app.middleware("http")
async def method_override(request: Request, call_next):
    """
    Allows HTML forms to simulate PUT via a hidden query parameter `_method=PUT`.
    """
    if request.method == "POST":
        query = request.url.query
        if "_method=PUT" in query:
            request.scope["method"] = "PUT"
    response = await call_next(request)
    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)