import uuid
import sqlite3
import difflib
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request, status, Form, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, validator

from jinja2 import Environment, select_autoescape

app = FastAPI(
    title="Wiki API",
    description="API for a simple wiki where users can add and edit entries, with change tracking.",
    version="1.0.0",
)

DB_PATH = "db.sqlite3"

# Limits to mitigate resource exhaustion
MAX_TITLE_LENGTH = 200
MAX_CONTENT_LENGTH = 5000
MAX_SUMMARY_LENGTH = 200
MAX_ENTRIES_RETURNED = 100
MAX_EDITS_RETURNED = 100

def get_db():
    # Allow connections to be shared across threads
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
            last_modified_by TEXT,
            last_modified_at TEXT
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
            timestamp TEXT NOT NULL,
            FOREIGN KEY(entry_id) REFERENCES entries(id)
        )
        """
    )
    conn.commit()
    conn.close()

@app.on_event("startup")
def startup_event():
    init_db()

# Jinja2 environment with autoescaping for HTML
jinja_env = Environment(autoescape=select_autoescape(enabled_extensions=("html", "htm", "xml")))

def render_template(template_str: str, **context) -> str:
    template = jinja_env.from_string(template_str)
    return template.render(**context)

# Pydantic models with size constraints
class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: Optional[str] = Field(default=None, alias="lastModifiedBy")
    lastModifiedAt: Optional[datetime] = Field(default=None, alias="lastModifiedAt")

class NewEntry(BaseModel):
    title: str = Field(..., max_length=MAX_TITLE_LENGTH)
    content: str = Field(..., max_length=MAX_CONTENT_LENGTH)
    createdBy: str = Field(..., max_length=MAX_TITLE_LENGTH)

    @validator("title", "content", "createdBy")
    def not_blank(cls, v):
        if not v.strip():
            raise ValueError("must not be blank")
        return v

class UpdateEntry(BaseModel):
    content: str = Field(..., max_length=MAX_CONTENT_LENGTH)
    modifiedBy: str = Field(..., max_length=MAX_TITLE_LENGTH)
    summary: Optional[str] = Field(default=None, max_length=MAX_SUMMARY_LENGTH)

    @validator("content", "modifiedBy")
    def not_blank(cls, v):
        if not v.strip():
            raise ValueError("must not be blank")
        return v

# HTML templates (autoescaping enabled, no safe filter)
LIST_TEMPLATE = """
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
    <h2>Create New Entry</h2>
    <form action="/entries/form" method="post">
        <label>Title: <input type="text" name="title" required maxlength="{{ max_title }}"></label><br>
        <label>Content:<br><textarea name="content" rows="10" cols="50" required maxlength="{{ max_content }}"></textarea></label><br>
        <label>Created By: <input type="text" name="createdBy" required maxlength="{{ max_title }}"></label><br>
        <button type="submit">Create</button>
    </form>
</body>
</html>
"""

ENTRY_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ entry.title }}</title>
</head>
<body>
    <h1>{{ entry.title }}</h1>
    <p><em>Last modified by {{ entry.lastModifiedBy }} at {{ entry.lastModifiedAt }}</em></p>
    <pre>{{ entry.content }}</pre>
    <hr>
    <h2>Edit Entry</h2>
    <form action="/entries/{{ entry.id }}/form" method="post">
        <label>Content:<br><textarea name="content" rows="10" cols="50" required maxlength="{{ max_content }}">{{ entry.content }}</textarea></label><br>
        <label>Modified By: <input type="text" name="modifiedBy" required maxlength="{{ max_title }}"></label><br>
        <label>Summary: <input type="text" name="summary" maxlength="{{ max_summary }}"></label><br>
        <button type="submit">Update</button>
    </form>
    <p><a href="/entries/{{ entry.id }}/edits">View Edit History</a></p>
    <p><a href="/entries">Back to list</a></p>
</body>
</html>
"""

EDITS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Edit History for {{ entry.title }}</title>
    <style>
        pre {background:#f0f0f0; padding:10px;}
    </style>
</head>
<body>
    <h1>Edit History for "{{ entry.title }}"</h1>
    {% for edit in edits %}
        <div>
            <h3>Edit #{{ loop.index }} - {{ edit.timestamp }} by {{ edit.modified_by }}</h3>
            {% if edit.summary %}
                <p><strong>Summary:</strong> {{ edit.summary }}</p>
            {% endif %}
            {% if edit.diff %}
                <pre>{{ edit.diff }}</pre>
            {% endif %}
        </div>
        <hr>
    {% endfor %}
    <p><a href="/entries/{{ entry.id }}">Back to entry</a></p>
    <p><a href="/entries">Back to list</a></p>
</body>
</html>
"""

def fetch_entry(entry_id: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
    entry = cur.fetchone()
    conn.close()
    return entry

def fetch_all_entries(limit: int = MAX_ENTRIES_RETURNED) -> List[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, title FROM entries ORDER BY title LIMIT ?", (limit,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def fetch_edits(entry_id: str, limit: int = MAX_EDITS_RETURNED) -> List[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT content, modified_by, summary, timestamp
        FROM edits
        WHERE entry_id = ?
        ORDER BY timestamp ASC
        LIMIT ?
        """,
        (entry_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows

@app.get(
    "/entries",
    response_class=HTMLResponse,
    summary="Get a list of all entries with their titles and links to respective entries",
)
def list_entries():
    entries = fetch_all_entries()
    rendered = render_template(
        LIST_TEMPLATE,
        entries=entries,
        max_title=MAX_TITLE_LENGTH,
        max_content=MAX_CONTENT_LENGTH,
    )
    return HTMLResponse(content=rendered)

@app.post(
    "/entries",
    response_model=Entry,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new entry",
)
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
    cur.execute(
        """
        INSERT INTO edits (entry_id, content, modified_by, summary, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entry_id, new_entry.content, new_entry.createdBy, "Created entry", now),
    )
    conn.commit()
    conn.close()
    return Entry(
        id=entry_id,
        title=new_entry.title,
        content=new_entry.content,
        lastModifiedBy=new_entry.createdBy,
        lastModifiedAt=now,
    )

# Form handling for HTML UI (separate from JSON API)
@app.post("/entries/form")
def create_entry_form(
    title: str = Form(..., max_length=MAX_TITLE_LENGTH),
    content: str = Form(..., max_length=MAX_CONTENT_LENGTH),
    createdBy: str = Form(..., max_length=MAX_TITLE_LENGTH),
):
    # Reuse the same logic as JSON endpoint
    new_entry = NewEntry(title=title, content=content, createdBy=createdBy)
    entry = create_entry(new_entry)
    return RedirectResponse(url=f"/entries/{entry.id}", status_code=status.HTTP_303_SEE_OTHER)

@app.get(
    "/entries/{entryId}",
    response_class=HTMLResponse,
    summary="Get a specific entry",
)
def get_entry(entryId: str):
    row = fetch_entry(entryId)
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    entry_data = {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "lastModifiedBy": row["last_modified_by"],
        "lastModifiedAt": row["last_modified_at"],
    }
    rendered = render_template(
        ENTRY_TEMPLATE,
        entry=entry_data,
        max_content=MAX_CONTENT_LENGTH,
        max_title=MAX_TITLE_LENGTH,
        max_summary=MAX_SUMMARY_LENGTH,
    )
    return HTMLResponse(content=rendered)

@app.post("/entries/{entryId}/form")
def update_entry_form(
    entryId: str,
    content: str = Form(..., max_length=MAX_CONTENT_LENGTH),
    modifiedBy: str = Form(..., max_length=MAX_TITLE_LENGTH),
    summary: Optional[str] = Form(None, max_length=MAX_SUMMARY_LENGTH),
):
    update = UpdateEntry(content=content, modifiedBy=modifiedBy, summary=summary)
    updated_entry = update_entry(entryId, update)
    return RedirectResponse(url=f"/entries/{updated_entry.id}", status_code=status.HTTP_303_SEE_OTHER)

@app.put(
    "/entries/{entryId}",
    response_model=Entry,
    summary="Update an existing entry",
)
def update_entry(entryId: str, update: UpdateEntry):
    entry = fetch_entry(entryId)
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
        (update.content, update.modifiedBy, now, entryId),
    )
    cur.execute(
        """
        INSERT INTO edits (entry_id, content, modified_by, summary, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            entryId,
            update.content,
            update.modifiedBy,
            update.summary or "",
            now,
        ),
    )
    conn.commit()
    conn.close()
    return Entry(
        id=entryId,
        title=entry["title"],
        content=update.content,
        lastModifiedBy=update.modifiedBy,
        lastModifiedAt=now,
    )

@app.get(
    "/entries/{entryId}/edits",
    response_class=HTMLResponse,
    summary="View the history of edits for a specific entry",
)
def view_edits(entryId: str):
    entry = fetch_entry(entryId)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    edits_rows = fetch_edits(entryId)
    edits = []
    previous_content = ""
    for row in edits_rows:
        current_content = row["content"]
        diff = "\n".join(
            difflib.unified_diff(
                previous_content.splitlines(),
                current_content.splitlines(),
                fromfile="prev",
                tofile="curr",
                lineterm="",
            )
        )
        edits.append(
            {
                "content": current_content,
                "modified_by": row["modified_by"],
                "summary": row["summary"],
                "timestamp": row["timestamp"],
                "diff": diff if diff else "(no changes)",
            }
        )
        previous_content = current_content
    rendered = render_template(
        EDITS_TEMPLATE,
        entry={"id": entryId, "title": entry["title"]},
        edits=edits,
    )
    return HTMLResponse(content=rendered)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)