import uuid
import sqlite3
import datetime
import difflib
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request, Depends, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from jinja2 import Template

# ---------- Database Setup ----------
DB_PATH = "db.sqlite3"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row

def init_db():
    with conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_by TEXT NOT NULL,
            last_modified_by TEXT NOT NULL,
            last_modified_at TEXT NOT NULL
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS edits (
            id TEXT PRIMARY KEY,
            entry_id TEXT NOT NULL,
            content TEXT NOT NULL,
            modified_by TEXT NOT NULL,
            summary TEXT,
            edited_at TEXT NOT NULL,
            FOREIGN KEY(entry_id) REFERENCES entries(id)
        )
        """)

init_db()

def get_db():
    return conn

# ---------- Pydantic Models ----------
class EntryModel(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str = Field(..., alias="lastModifiedBy")
    lastModifiedAt: datetime.datetime = Field(..., alias="lastModifiedAt")

    class Config:
        orm_mode = True
        allow_population_by_field_name = True

class NewEntry(BaseModel):
    title: str
    content: str
    createdBy: str = Field(..., alias="createdBy")

class UpdateEntry(BaseModel):
    content: str
    modifiedBy: str = Field(..., alias="modifiedBy")
    summary: Optional[str] = None

# ---------- HTML Templates ----------
entries_list_template = Template("""
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
  Title: <input type="text" name="title"><br>
  Content:<br><textarea name="content" rows="10" cols="50"></textarea><br>
  Created By: <input type="text" name="createdBy"><br>
  <button type="submit">Create</button>
</form>
</body>
</html>
""")

entry_detail_template = Template("""
<!DOCTYPE html>
<html>
<head><title>{{ entry.title }}</title></head>
<body>
<h1>{{ entry.title }}</h1>
<p><em>Last modified by {{ entry.last_modified_by }} at {{ entry.last_modified_at }}</em></p>
<div style="white-space: pre-wrap; border:1px solid #ccc; padding:10px;">{{ entry.content }}</div>

<h2>Contributors</h2>
<ul>
{% for contributor in contributors %}
  <li>{{ contributor }}</li>
{% endfor %}
</ul>

<h2>Edit Entry</h2>
<form method="post" action="/entries/{{ entry.id }}?_method=PUT">
  Content:<br><textarea name="content" rows="10" cols="50">{{ entry.content }}</textarea><br>
  Modified By: <input type="text" name="modifiedBy"><br>
  Summary: <input type="text" name="summary"><br>
  <button type="submit">Update</button>
</form>

<p><a href="/entries/{{ entry.id }}/edits">View edit history</a></p>
<p><a href="/entries">Back to list</a></p>
</body>
</html>
""")

edits_history_template = Template("""
<!DOCTYPE html>
<html>
<head><title>Edit History for {{ entry.title }}</title></head>
<body>
<h1>Edit History for "{{ entry.title }}"</h1>
{% for edit in edits %}
  <div style="border:1px solid #aaa; margin-bottom:10px; padding:5px;">
    <p><strong>Edited by:</strong> {{ edit.modified_by }} at {{ edit.edited_at }}</p>
    {% if edit.summary %}
      <p><strong>Summary:</strong> {{ edit.summary }}</p>
    {% endif %}
    <pre style="background:#f8f8f8; padding:5px;">{{ edit.diff }}</pre>
  </div>
{% endfor %}
<p><a href="/entries/{{ entry.id }}">Back to entry</a></p>
</body>
</html>
""")

# ---------- FastAPI App ----------
app = FastAPI()

# Helper to fetch entry
def fetch_entry(entry_id: str):
    cur = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    return row

# ---------- Routes ----------
@app.get("/entries", response_class=HTMLResponse)
def list_entries():
    cur = conn.execute("SELECT id, title FROM entries ORDER BY title")
    entries = cur.fetchall()
    return entries_list_template.render(entries=entries)

@app.post("/entries")
def create_entry(request: Request):
    # Accept both JSON and form data
    if request.headers.get("content-type", "").startswith("application/json"):
        data = request.json()
    else:
        # form data
        data = request.form()
    # FastAPI automatically parses JSON body if we declare a model, but we need manual handling
    # Use dependency injection for JSON body
    # Simpler: declare model
    # We'll let FastAPI handle JSON via model
    # This endpoint will be called via JSON in API usage
    # For HTML form fallback, we parse form manually
    # We'll handle both cases
    try:
        payload = request.json()
    except Exception:
        # fallback to form
        form = request.form()
        payload = {
            "title": request.form().get("title"),
            "content": request.form().get("content"),
            "createdBy": request.form().get("createdBy")
        }
    # Validate using Pydantic
    new_entry = NewEntry(**payload)
    entry_id = str(uuid.uuid4())
    now = datetime.datetime.utcnow().isoformat()
    with conn:
        conn.execute(
            """INSERT INTO entries (id, title, content, created_by, last_modified_by, last_modified_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entry_id, new_entry.title, new_entry.content, new_entry.createdBy,
             new_entry.createdBy, now)
        )
        # initial edit record
        edit_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO edits (id, entry_id, content, modified_by, summary, edited_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (edit_id, entry_id, new_entry.content, new_entry.createdBy, "Created", now)
        )
    entry = {
        "id": entry_id,
        "title": new_entry.title,
        "content": new_entry.content,
        "lastModifiedBy": new_entry.createdBy,
        "lastModifiedAt": now
    }
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=entry)

@app.get("/entries/{entry_id}", response_class=HTMLResponse)
def get_entry(entry_id: str):
    entry_row = fetch_entry(entry_id)
    # contributors from edits
    cur = conn.execute(
        "SELECT DISTINCT modified_by FROM edits WHERE entry_id = ?", (entry_id,)
    )
    contributors = [r["modified_by"] for r in cur.fetchall()]
    return entry_detail_template.render(entry=entry_row, contributors=contributors)

@app.put("/entries/{entry_id}")
def update_entry(entry_id: str, update: UpdateEntry):
    entry_row = fetch_entry(entry_id)  # raises 404 if not found
    now = datetime.datetime.utcnow().isoformat()
    with conn:
        conn.execute(
            """UPDATE entries SET content = ?, last_modified_by = ?, last_modified_at = ?
               WHERE id = ?""",
            (update.content, update.modifiedBy, now, entry_id)
        )
        edit_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO edits (id, entry_id, content, modified_by, summary, edited_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (edit_id, entry_id, update.content, update.modifiedBy, update.summary, now)
        )
    # Return updated entry JSON
    cur = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
    row = cur.fetchone()
    entry = {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "lastModifiedBy": row["last_modified_by"],
        "lastModifiedAt": row["last_modified_at"]
    }
    return entry

@app.get("/entries/{entry_id}/edits", response_class=HTMLResponse)
def get_edits(entry_id: str):
    entry_row = fetch_entry(entry_id)  # ensure exists
    cur = conn.execute(
        """SELECT * FROM edits WHERE entry_id = ? ORDER BY edited_at ASC""",
        (entry_id,)
    )
    edits = cur.fetchall()
    # Build diff for each edit compared to previous version
    edit_list = []
    previous_content = None
    for edit in edits:
        current_content = edit["content"]
        if previous_content is None:
            diff = current_content  # first version, show full content
        else:
            diff_lines = difflib.unified_diff(
                previous_content.splitlines(),
                current_content.splitlines(),
                fromfile="prev",
                tofile="curr",
                lineterm=""
            )
            diff = "\n".join(diff_lines)
        edit_list.append({
            "modified_by": edit["modified_by"],
            "edited_at": edit["edited_at"],
            "summary": edit["summary"],
            "diff": diff
        })
        previous_content = current_content
    return edits_history_template.render(entry=entry_row, edits=edit_list)

# ---------- HTML Form Method Override ----------
@app.post("/entries/{entry_id}")
def method_override(entry_id: str, request: Request):
    # Support HTML forms that cannot send PUT; use query param _method=PUT
    method = request.query_params.get("_method")
    if method and method.upper() == "PUT":
        form = request.form()
        # FastAPI cannot directly read form in sync function; use async
        # We'll define async version below
        raise HTTPException(status_code=400, detail="Use async endpoint for form override")
    raise HTTPException(status_code=405, detail="Method Not Allowed")

# Async version for form override
@app.post("/entries/{entry_id}")
async def method_override_async(entry_id: str, request: Request):
    method = request.query_params.get("_method")
    if method and method.upper() == "PUT":
        form = await request.form()
        content = form.get("content")
        modified_by = form.get("modifiedBy")
        summary = form.get("summary")
        if not content or not modified_by:
            raise HTTPException(status_code=400, detail="Missing fields")
        update = UpdateEntry(content=content, modifiedBy=modified_by, summary=summary)
        return await update_entry(entry_id, update)
    raise HTTPException(status_code=405, detail="Method Not Allowed")

# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)