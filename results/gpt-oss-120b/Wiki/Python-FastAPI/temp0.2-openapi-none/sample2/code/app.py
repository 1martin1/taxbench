import uuid
import sqlite3
import difflib
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from jinja2 import Template

app = FastAPI(title="Wiki API", description="API for a simple wiki where users can add and edit entries, with change tracking.", version="1.0.0")

DB_PATH = "db.sqlite3"


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
    conn.close()


@app.on_event("startup")
def startup():
    init_db()


# Pydantic models
class NewEntry(BaseModel):
    title: str
    content: str
    createdBy: str = Field(..., alias="createdBy")


class UpdateEntry(BaseModel):
    content: str
    modifiedBy: str = Field(..., alias="modifiedBy")
    summary: Optional[str] = None


class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str = Field(..., alias="lastModifiedBy")
    lastModifiedAt: datetime = Field(..., alias="lastModifiedAt")


# Templates
LIST_TEMPLATE = Template(
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
            <label>Title: <input type="text" name="title" required></label><br>
            <label>Content:<br><textarea name="content" rows="10" cols="50" required></textarea></label><br>
            <label>Created By: <input type="text" name="createdBy" required></label><br>
            <button type="submit">Create</button>
        </form>
    </body>
    </html>
    """
)

ENTRY_TEMPLATE = Template(
    """
    <!DOCTYPE html>
    <html>
    <head><title>{{ entry.title }}</title></head>
    <body>
        <h1>{{ entry.title }}</h1>
        <p><em>Last modified by {{ entry.last_modified_by }} at {{ entry.last_modified_at }}</em></p>
        <div style="white-space: pre-wrap; border:1px solid #ccc; padding:10px;">{{ entry.content }}</div>
        <h2>Edit Entry</h2>
        <form method="post" action="/entries/{{ entry.id }}?_method=PUT">
            <label>Content:<br><textarea name="content" rows="10" cols="50" required>{{ entry.content }}</textarea></label><br>
            <label>Modified By: <input type="text" name="modifiedBy" required></label><br>
            <label>Summary: <input type="text" name="summary"></label><br>
            <button type="submit">Update</button>
        </form>
        <p><a href="/entries/{{ entry.id }}/edits">View Edit History</a></p>
        <p><a href="/entries">Back to list</a></p>
    </body>
    </html>
    """
)

EDITS_TEMPLATE = Template(
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


# Helper functions
def fetch_entry(entry_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
    row = cur.fetchone()
    conn.close()
    return row


def fetch_all_entries():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM entries ORDER BY title")
    rows = cur.fetchall()
    conn.close()
    return rows


def fetch_edits(entry_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM edits WHERE entry_id = ? ORDER BY edited_at ASC", (entry_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def insert_edit(entry_id: str, content: str, modified_by: str, summary: Optional[str]):
    edit_id = str(uuid.uuid4())
    edited_at = datetime.utcnow().isoformat()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO edits (id, entry_id, content, modified_by, summary, edited_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (edit_id, entry_id, content, modified_by, summary, edited_at),
    )
    conn.commit()
    conn.close()


def update_entry(entry_id: str, title: str, content: str, modified_by: str):
    now = datetime.utcnow().isoformat()
    conn = get_db()
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
    conn.close()


# Routes
@app.get("/entries", response_class=HTMLResponse)
def list_entries():
    entries = fetch_all_entries()
    html = LIST_TEMPLATE.render(entries=entries)
    return HTMLResponse(content=html)


@app.post("/entries", response_model=Entry, status_code=status.HTTP_201_CREATED)
def create_entry(new_entry: NewEntry):
    entry_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entry_id, new_entry.title, new_entry.content, new_entry.createdBy, now),
    )
    conn.commit()
    conn.close()
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
    # Update entry
    update_entry(entry_id, row["title"], update.content, update.modifiedBy)
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
    # Build diff for each edit compared to previous content
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


# Support HTML form method override for PUT (since browsers only support GET/POST)
@app.middleware("http")
async def method_override(request: Request, call_next):
    if request.method == "POST":
        # Check query param _method
        query = request.url.query
        if "_method=PUT" in query:
            request.scope["method"] = "PUT"
    response = await call_next(request)
    return response

# Run with: uvicorn this_file_name:app --host 0.0.0.0 --port 5000