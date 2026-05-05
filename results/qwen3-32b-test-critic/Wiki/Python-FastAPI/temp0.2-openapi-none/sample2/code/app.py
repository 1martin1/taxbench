from fastapi import FastAPI, HTTPException, Path
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from datetime import datetime
import sqlite3
import difflib
import uuid

app = FastAPI()

# Database setup
def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            last_modified_by TEXT NOT NULL,
            last_modified_at TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL,
            content TEXT NOT NULL,
            modified_by TEXT NOT NULL,
            modified_at TEXT NOT NULL,
            summary TEXT NOT NULL,
            diff TEXT NOT NULL,
            FOREIGN KEY (entry_id) REFERENCES entries(id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Pydantic models
class Entry(BaseModel):
    id: str
    title: str
    content: str
    last_modified_by: str
    last_modified_at: str

class NewEntry(BaseModel):
    title: str
    content: str
    created_by: str

class UpdateEntry(BaseModel):
    content: str
    modified_by: str
    summary: str

# HTML templates
entries_template = """
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
</body>
</html>
"""

entry_template = """
<!DOCTYPE html>
<html>
<head><title>{{ entry.title }}</title></head>
<body>
<h1>{{ entry.title }}</h1>
<p>{{ entry.content }}</p>
<p>Last modified by: {{ entry.last_modified_by }} at {{ entry.last_modified_at }}</p>
<a href="/entries/{{ entry.id }}/edits">View edits</a>
</body>
</html>
"""

edits_template = """
<!DOCTYPE html>
<html>
<head><title>Edits for {{ entry_id }}</title></head>
<body>
<h1>Edits for Entry {{ entry_id }}</h1>
<ul>
{% for edit in edits %}
<li>
<h3>Edit by {{ edit.modified_by }} at {{ edit.modified_at }}</h3>
<p><strong>Summary:</strong> {{ edit.summary }}</p>
<pre>{{ edit.diff }}</pre>
</li>
{% endfor %}
</ul>
</body>
</html>
"""

# Helper functions
def get_db_connection():
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    return conn

def generate_diff(old_content, new_content):
    differ = difflib.ndiff(old_content.splitlines(), new_content.splitlines())
    return '\n'.join(differ)

# Endpoints
@app.get("/entries", response_class=HTMLResponse)
async def list_entries():
    conn = get_db_connection()
    entries = conn.execute('SELECT * FROM entries').fetchall()
    conn.close()
    entries_list = [dict(entry) for entry in entries]
    return entries_template

@app.post("/entries", response_model=Entry, status_code=201)
async def create_entry(entry: NewEntry):
    entry_id = str(uuid.uuid4())
    current_time = datetime.utcnow().isoformat()
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (entry_id, entry.title, entry.content, entry.created_by, current_time))
    conn.commit()
    conn.close()
    return Entry(
        id=entry_id,
        title=entry.title,
        content=entry.content,
        last_modified_by=entry.created_by,
        last_modified_at=current_time
    )

@app.get("/entries/{entry_id}", response_class=HTMLResponse)
async def get_entry(entry_id: str = Path(...)):
    conn = get_db_connection()
    entry = conn.execute('SELECT * FROM entries WHERE id = ?', (entry_id,)).fetchone()
    conn.close()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry_template.format(**dict(entry))

@app.put("/entries/{entry_id}", response_model=Entry)
async def update_entry(entry_id: str = Path(...), update_data: UpdateEntry = None):
    conn = get_db_connection()
    entry = conn.execute('SELECT * FROM entries WHERE id = ?', (entry_id,)).fetchone()
    if not entry:
        conn.close()
        raise HTTPException(status_code=404, detail="Entry not found")
    old_content = entry['content']
    new_content = update_data.content
    diff = generate_diff(old_content, new_content)
    current_time = datetime.utcnow().isoformat()
    conn.execute('''
        INSERT INTO edits (entry_id, content, modified_by, modified_at, summary, diff)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (entry_id, old_content, update_data.modified_by, current_time, update_data.summary, diff))
    conn.execute('''
        UPDATE entries
        SET content = ?, last_modified_by = ?, last_modified_at = ?
        WHERE id = ?
    ''', (new_content, update_data.modified_by, current_time, entry_id))
    conn.commit()
    updated_entry = conn.execute('SELECT * FROM entries WHERE id = ?', (entry_id,)).fetchone()
    conn.close()
    return Entry(**dict(updated_entry))

@app.get("/entries/{entry_id}/edits", response_class=HTMLResponse)
async def get_edits(entry_id: str = Path(...)):
    conn = get_db_connection()
    entry = conn.execute('SELECT * FROM entries WHERE id = ?', (entry_id,)).fetchone()
    if not entry:
        conn.close()
        raise HTTPException(status_code=404, detail="Entry not found")
    edits = conn.execute('SELECT * FROM edits WHERE entry_id = ? ORDER BY modified_at DESC', (entry_id,)).fetchall()
    conn.close()
    edits_list = [dict(edit) for edit in edits]
    return edits_template.format(entry_id=entry_id, edits=edits_list)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)