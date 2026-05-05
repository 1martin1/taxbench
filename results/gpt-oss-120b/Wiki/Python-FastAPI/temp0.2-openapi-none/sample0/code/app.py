import uuid
import sqlite3
import datetime
import difflib
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from jinja2 import Environment, DictLoader, select_autoescape

# ---------- Database Setup ----------
DB_PATH = "db.sqlite3"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

def init_db():
    cur.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            last_modified_by TEXT NOT NULL,
            last_modified_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL,
            content TEXT NOT NULL,
            modified_by TEXT NOT NULL,
            summary TEXT,
            modified_at TEXT NOT NULL,
            FOREIGN KEY(entry_id) REFERENCES entries(id)
        )
    """)
    conn.commit()

init_db()

# ---------- Pydantic Models ----------
class NewEntry(BaseModel):
    title: str
    content: str
    createdBy: str = Field(..., alias="createdBy")

class UpdateEntry(BaseModel):
    content: str
    modifiedBy: str = Field(..., alias="modifiedBy")
    summary: Optional[str] = None

class EntryResponse(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str
    lastModifiedAt: datetime.datetime

# ---------- Jinja2 Templates ----------
templates = {
    "entries_list.html": """
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
    <form method="post" action="/entries" id="newEntryForm">
        <label>Title: <input type="text" name="title" required></label><br>
        <label>Content:<br><textarea name="content" rows="10" cols="50" required></textarea></label><br>
        <label>Created By: <input type="text" name="createdBy" required></label><br>
        <button type="submit">Create</button>
    </form>
    <script>
        // Submit form as JSON
        document.getElementById('newEntryForm').addEventListener('submit', async function(e){
            e.preventDefault();
            const form = e.target;
            const data = {
                title: form.title.value,
                content: form.content.value,
                createdBy: form.createdBy.value
            };
            const resp = await fetch(form.action, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            if (resp.ok) {
                location.reload();
            } else {
                const err = await resp.text();
                alert('Error: ' + err);
            }
        });
    </script>
</body>
</html>
""",
    "entry_detail.html": """
<!DOCTYPE html>
<html>
<head>
    <title>{{ entry.title }}</title>
</head>
<body>
    <h1>{{ entry.title }}</h1>
    <p><em>Last modified by {{ entry.last_modified_by }} at {{ entry.last_modified_at }}</em></p>
    <div style="white-space: pre-wrap; border:1px solid #ccc; padding:10px;">
        {{ entry.content }}
    </div>

    <h2>Edit Entry</h2>
    <form method="post" action="/entries/{{ entry.id }}" id="editForm">
        <label>Content:<br><textarea name="content" rows="10" cols="50" required>{{ entry.content }}</textarea></label><br>
        <label>Modified By: <input type="text" name="modifiedBy" required></label><br>
        <label>Summary: <input type="text" name="summary"></label><br>
        <button type="submit">Update</button>
    </form>

    <h2>History</h2>
    <a href="/entries/{{ entry.id }}/edits">View edit history</a>

    <script>
        document.getElementById('editForm').addEventListener('submit', async function(e){
            e.preventDefault();
            const form = e.target;
            const data = {
                content: form.content.value,
                modifiedBy: form.modifiedBy.value,
                summary: form.summary.value
            };
            const resp = await fetch(form.action, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            if (resp.ok) {
                location.reload();
            } else {
                const err = await resp.text();
                alert('Error: ' + err);
            }
        });
    </script>
</body>
</html>
""",
    "edits_history.html": """
<!DOCTYPE html>
<html>
<head>
    <title>Edit History for {{ entry.title }}</title>
</head>
<body>
    <h1>Edit History for "{{ entry.title }}"</h1>
    <a href="/entries/{{ entry.id }}">Back to entry</a>
    <ul>
    {% for edit in edits %}
        <li>
            <strong>{{ edit.modified_at }} by {{ edit.modified_by }}</strong>
            {% if edit.summary %}
                <em> - {{ edit.summary }}</em>
            {% endif %}
            <pre style="background:#f8f8f8; padding:5px;">{{ edit.diff }}</pre>
        </li>
    {% endfor %}
    </ul>
</body>
</html>
"""
}
jinja_env = Environment(
    loader=DictLoader(templates),
    autoescape=select_autoescape(['html', 'xml'])
)

# ---------- FastAPI App ----------
app = FastAPI()

# ---------- Helper Functions ----------
def get_entry(entry_id: str):
    cur.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
    row = cur.fetchone()
    if row:
        return dict(row)
    return None

def get_all_entries():
    cur.execute("SELECT id, title FROM entries ORDER BY title")
    return [dict(r) for r in cur.fetchall()]

def insert_entry(entry_data: NewEntry):
    entry_id = str(uuid.uuid4())
    now = datetime.datetime.utcnow().isoformat()
    cur.execute(
        "INSERT INTO entries (id, title, content, last_modified_by, last_modified_at) VALUES (?,?,?,?,?)",
        (entry_id, entry_data.title, entry_data.content, entry_data.createdBy, now)
    )
    # initial edit record
    cur.execute(
        "INSERT INTO edits (entry_id, content, modified_by, summary, modified_at) VALUES (?,?,?,?,?)",
        (entry_id, entry_data.content, entry_data.createdBy, "Initial creation", now)
    )
    conn.commit()
    return entry_id

def update_entry(entry_id: str, update: UpdateEntry):
    entry = get_entry(entry_id)
    if not entry:
        return None
    now = datetime.datetime.utcnow().isoformat()
    cur.execute(
        "UPDATE entries SET content = ?, last_modified_by = ?, last_modified_at = ? WHERE id = ?",
        (update.content, update.modifiedBy, now, entry_id)
    )
    cur.execute(
        "INSERT INTO edits (entry_id, content, modified_by, summary, modified_at) VALUES (?,?,?,?,?)",
        (entry_id, update.content, update.modifiedBy, update.summary, now)
    )
    conn.commit()
    return get_entry(entry_id)

def get_edits(entry_id: str):
    cur.execute(
        "SELECT * FROM edits WHERE entry_id = ? ORDER BY modified_at ASC",
        (entry_id,)
    )
    return [dict(r) for r in cur.fetchall()]

def compute_diff(old: str, new: str) -> str:
    diff = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        lineterm='',
        fromfile='previous',
        tofile='current'
    )
    return '\n'.join(diff)

# ---------- Routes ----------
@app.get("/entries", response_class=HTMLResponse)
def list_entries():
    entries = get_all_entries()
    template = jinja_env.get_template("entries_list.html")
    return template.render(entries=entries)

@app.post("/entries", response_model=EntryResponse, status_code=status.HTTP_201_CREATED)
def create_entry(entry: NewEntry):
    entry_id = insert_entry(entry)
    created = get_entry(entry_id)
    return EntryResponse(
        id=created["id"],
        title=created["title"],
        content=created["content"],
        lastModifiedBy=created["last_modified_by"],
        lastModifiedAt=datetime.datetime.fromisoformat(created["last_modified_at"])
    )

@app.get("/entries/{entry_id}", response_class=HTMLResponse)
def get_entry_detail(entry_id: str):
    entry = get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    template = jinja_env.get_template("entry_detail.html")
    return template.render(entry=entry)

@app.put("/entries/{entry_id}", response_model=EntryResponse)
def edit_entry(entry_id: str, update: UpdateEntry):
    updated = update_entry(entry_id, update)
    if not updated:
        raise HTTPException(status_code=404, detail="Entry not found")
    return EntryResponse(
        id=updated["id"],
        title=updated["title"],
        content=updated["content"],
        lastModifiedBy=updated["last_modified_by"],
        lastModifiedAt=datetime.datetime.fromisoformat(updated["last_modified_at"])
    )

@app.get("/entries/{entry_id}/edits", response_class=HTMLResponse)
def entry_edits(entry_id: str):
    entry = get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    edits = get_edits(entry_id)
    # compute diffs
    for i, edit in enumerate(edits):
        prev_content = edits[i-1]["content"] if i > 0 else ""
        edit["diff"] = compute_diff(prev_content, edit["content"])
    template = jinja_env.get_template("edits_history.html")
    return template.render(entry=entry, edits=edits)

# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)