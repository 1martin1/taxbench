import uuid
import sqlite3
import datetime
import difflib
import threading
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict, StringConstraints
from jinja2 import Environment, select_autoescape

# ---------- Database Setup ----------
DB_PATH = "db.sqlite3"
# Global connection (shared) – use a lock for thread‑safety
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
db_lock = threading.Lock()


def init_db() -> None:
    with conn:
        conn.execute(
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
        conn.execute(
            """
        CREATE TABLE IF NOT EXISTS edits (
            id TEXT PRIMARY KEY,
            entry_id TEXT NOT NULL,
            content TEXT NOT NULL,
            modified_by TEXT NOT NULL,
            summary TEXT,
            edited_at TEXT NOT NULL,
            FOREIGN KEY(entry_id) REFERENCES entries(id)
        )
        """
        )


init_db()


def get_connection() -> sqlite3.Connection:
    """Return the shared SQLite connection."""
    return conn


# ---------- Pydantic Models ----------
class EntryModel(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str = Field(alias="lastModifiedBy")
    lastModifiedAt: datetime.datetime = Field(alias="lastModifiedAt")

    model_config = ConfigDict(populate_by_name=True)


class NewEntry(BaseModel):
    title: str = Field(..., max_length=200)
    content: str = Field(..., max_length=5000)
    createdBy: str = Field(..., alias="createdBy", max_length=100)

    model_config = ConfigDict(populate_by_name=True)


class UpdateEntry(BaseModel):
    content: str = Field(..., max_length=5000)
    modifiedBy: str = Field(..., alias="modifiedBy", max_length=100)
    summary: Optional[str] = Field(None, max_length=300)

    model_config = ConfigDict(populate_by_name=True)


# ---------- Jinja2 Templates (auto‑escaping enabled) ----------
jinja_env = Environment(autoescape=select_autoescape(["html", "xml"]))

entries_list_template = jinja_env.from_string(
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
  Title: <input type="text" name="title"><br>
  Content:<br><textarea name="content" rows="10" cols="50"></textarea><br>
  Created By: <input type="text" name="createdBy"><br>
  <button type="submit">Create</button>
</form>
</body>
</html>
"""
)

entry_detail_template = jinja_env.from_string(
    """
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
"""
)

edits_history_template = jinja_env.from_string(
    """
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
"""
)

# ---------- FastAPI App ----------
app = FastAPI()


def fetch_entry(entry_id: str) -> sqlite3.Row:
    cur = get_connection().execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    return row


# ---------- Routes ----------
@app.get("/entries", response_class=HTMLResponse)
async def list_entries():
    cur = get_connection().execute("SELECT id, title FROM entries ORDER BY title")
    entries = [dict(row) for row in cur.fetchall()]
    return entries_list_template.render(entries=entries)


@app.post("/entries")
async def create_entry(request: Request):
    # Determine payload source (JSON or form)
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
    else:
        form = await request.form()
        payload = {
            "title": form.get("title"),
            "content": form.get("content"),
            "createdBy": form.get("createdBy"),
        }

    try:
        new_entry = NewEntry(**payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    entry_id = str(uuid.uuid4())
    now_iso = datetime.datetime.utcnow().isoformat()

    with db_lock:
        conn = get_connection()
        conn.execute(
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
                now_iso,
            ),
        )
        edit_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO edits (id, entry_id, content, modified_by, summary, edited_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (edit_id, entry_id, new_entry.content, new_entry.createdBy, "Created", now_iso),
        )

    entry_dict = {
        "id": entry_id,
        "title": new_entry.title,
        "content": new_entry.content,
        "lastModifiedBy": new_entry.createdBy,
        "lastModifiedAt": now_iso,
    }
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=entry_dict)


@app.get("/entries/{entry_id}", response_class=HTMLResponse)
async def get_entry(entry_id: str):
    entry_row = fetch_entry(entry_id)
    entry = dict(entry_row)

    cur = get_connection().execute(
        "SELECT DISTINCT modified_by FROM edits WHERE entry_id = ?", (entry_id,)
    )
    contributors = [r["modified_by"] for r in cur.fetchall()]

    return entry_detail_template.render(entry=entry, contributors=contributors)


@app.put("/entries/{entry_id}")
async def update_entry(entry_id: str, update: UpdateEntry):
    # Ensure entry exists
    fetch_entry(entry_id)

    now_iso = datetime.datetime.utcnow().isoformat()
    with db_lock:
        conn = get_connection()
        conn.execute(
            """
            UPDATE entries
            SET content = ?, last_modified_by = ?, last_modified_at = ?
            WHERE id = ?
            """,
            (update.content, update.modifiedBy, now_iso, entry_id),
        )
        edit_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO edits (id, entry_id, content, modified_by, summary, edited_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (edit_id, entry_id, update.content, update.modifiedBy, update.summary, now_iso),
        )

    # Return the updated entry as JSON
    cur = get_connection().execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
    row = cur.fetchone()
    entry_dict = {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "lastModifiedBy": row["last_modified_by"],
        "lastModifiedAt": row["last_modified_at"],
    }
    return entry_dict


@app.get("/entries/{entry_id}/edits", response_class=HTMLResponse)
async def get_edits(entry_id: str):
    # Verify entry exists
    entry_row = fetch_entry(entry_id)
    entry = dict(entry_row)

    cur = get_connection().execute(
        """
        SELECT * FROM edits
        WHERE entry_id = ?
        ORDER BY edited_at ASC
        """,
        (entry_id,),
    )
    edits = cur.fetchall()

    edit_list: List[dict] = []
    previous_content: Optional[str] = None
    for edit in edits:
        current_content = edit["content"]
        if previous_content is None:
            diff = current_content  # first version: show full content
        else:
            diff_lines = difflib.unified_diff(
                previous_content.splitlines(),
                current_content.splitlines(),
                fromfile="prev",
                tofile="curr",
                lineterm="",
            )
            diff = "\n".join(diff_lines)
        edit_list.append(
            {
                "modified_by": edit["modified_by"],
                "edited_at": edit["edited_at"],
                "summary": edit["summary"],
                "diff": diff,
            }
        )
        previous_content = current_content

    return edits_history_template.render(entry=entry, edits=edit_list)


# ---------- HTML Form Method Override ----------
@app.post("/entries/{entry_id}")
async def method_override(entry_id: str, request: Request):
    """
    HTML forms cannot send PUT/DELETE. This endpoint looks for a query parameter
    `_method=PUT` and, if present, treats the request as an update.
    """
    method = request.query_params.get("_method")
    if method and method.upper() == "PUT":
        form = await request.form()
        payload = {
            "content": form.get("content"),
            "modifiedBy": form.get("modifiedBy"),
            "summary": form.get("summary"),
        }
        try:
            update = UpdateEntry(**payload)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return await update_entry(entry_id, update)

    raise HTTPException(status_code=405, detail="Method Not Allowed")


# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)