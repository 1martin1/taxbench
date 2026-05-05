import sqlite3
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from datetime import datetime
import uuid
from jinja2 import Environment, select_autoescape

app = FastAPI()

class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str
    lastModifiedAt: datetime

class NewEntry(BaseModel):
    title: str
    content: str = Field(..., max_length=100000)
    createdBy: str

class UpdateEntry(BaseModel):
    content: str = Field(..., max_length=100000)
    modifiedBy: str
    summary: str

# Jinja2 templates with auto-escaping
env = Environment(autoescape=select_autoescape(['html']))

entry_list_template = env.from_string("""
<html>
<body>
    <h1>Wiki Entries</h1>
    <ul>
        {% for entry in entries %}
        <li><a href="/entries/{{ entry.id }}">{{ entry.title|e }}</a></li>
        {% endfor %}
    </ul>
</body>
</html>
""")

entry_detail_template = env.from_string("""
<html>
<body>
    <h1>{{ title|e }}</h1>
    <div>{{ content|e }}</div>
    <p>Last modified by: {{ last_modified_by|e }} at {{ last_modified_at }}</p>
    <h2>Contributors</h2>
    <ul>
        {% for contributor in contributors %}
        <li>{{ contributor|e }}</li>
        {% endfor %}
    </ul>
    <a href="/entries/{{ entryId }}/edits">View edit history</a>
</body>
</html>
""")

edits_list_template = env.from_string("""
<html>
<body>
    <h1>Edit History</h1>
    {% for edit in edits %}
    <div>
        <h2>Edit by {{ edit.modifiedBy|e }} at {{ edit.createdAt }}</h2>
        <p><strong>Summary:</strong> {{ edit.summary|e }}</p>
        <h3>Old Content:</h3>
        <pre>{{ edit.oldContent|e }}</pre>
        <h3>New Content:</h3>
        <pre>{{ edit.newContent|e }}</pre>
    </div>
    <hr>
    {% endfor %}
</body>
</html>
""")

@app.on_event("startup")
def create_tables():
    with sqlite3.connect("db.sqlite3") as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                last_modified_by TEXT NOT NULL,
                last_modified_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS edits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT NOT NULL,
                old_content TEXT NOT NULL,
                new_content TEXT NOT NULL,
                modified_by TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (entry_id) REFERENCES entries (id)
            )
        """)
        conn.commit()

@app.get("/entries")
def list_entries():
    with sqlite3.connect("db.sqlite3") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, title FROM entries")
        entries = cursor.fetchall()
    
    entries_data = [{"id": id, "title": title} for id, title in entries]
    return entry_list_template.render(entries=entries_data)

@app.post("/entries", status_code=201)
def create_entry(entry: NewEntry):
    entry_id = str(uuid.uuid4())
    with sqlite3.connect("db.sqlite3") as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
            VALUES (?, ?, ?, ?, ?)
        """, (entry_id, entry.title, entry.content, entry.createdBy, datetime.now().isoformat()))
        conn.commit()
    
    return Entry(
        id=entry_id,
        title=entry.title,
        content=entry.content,
        lastModifiedBy=entry.createdBy,
        lastModifiedAt=datetime.now()
    )

@app.get("/entries/{entryId}")
def get_entry(entryId: str):
    with sqlite3.connect("db.sqlite3") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT title, content, last_modified_by, last_modified_at FROM entries WHERE id = ?", (entryId,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Entry not found")
        
        title, content, created_by, last_modified_at_str = row
        last_modified_at = datetime.fromisoformat(last_modified_at_str)
        
        cursor.execute("SELECT modified_by FROM edits WHERE entry_id = ?", (entryId,))
        modified_bys = [row[0] for row in cursor.fetchall()]
        contributors = list(set([created_by] + modified_bys))
    
    return entry_detail_template.render(
        title=title,
        content=content,
        last_modified_by=created_by,
        last_modified_at=last_modified_at,
        contributors=contributors,
        entryId=entryId
    )

@app.put("/entries/{entryId}")
def update_entry(entryId: str, update_data: UpdateEntry):
    with sqlite3.connect("db.sqlite3") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT content, last_modified_by FROM entries WHERE id = ?", (entryId,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Entry not found")
        
        old_content, last_modified_by = row
        
        cursor.execute("""
            INSERT INTO edits (entry_id, old_content, new_content, modified_by, summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (entryId, old_content, update_data.content, update_data.modifiedBy, update_data.summary, datetime.now().isoformat()))
        
        cursor.execute("""
            UPDATE entries
            SET content = ?, last_modified_by = ?, last_modified_at = ?
            WHERE id = ?
        """, (update_data.content, update_data.modifiedBy, datetime.now().isoformat(), entryId))
        
        conn.commit()
        
        cursor.execute("SELECT title, content, last_modified_by, last_modified_at FROM entries WHERE id = ?", (entryId,))
        row = cursor.fetchone()
        title, content, last_modified_by, last_modified_at_str = row
        last_modified_at = datetime.fromisoformat(last_modified_at_str)
    
    return Entry(
        id=entryId,
        title=title,
        content=content,
        lastModifiedBy=last_modified_by,
        lastModifiedAt=last_modified_at
    )

@app.get("/entries/{entryId}/edits")
def get_edits(entryId: str):
    with sqlite3.connect("db.sqlite3") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM edits WHERE entry_id = ? ORDER BY created_at DESC", (entryId,))
        rows = cursor.fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="Entry not found")
        
        edits = []
        for row in rows:
            edit_id, entry_id, old_content, new_content, modified_by, summary, created_at_str = row
            created_at = datetime.fromisoformat(created_at_str)
            edits.append({
                "id": edit_id,
                "entryId": entry_id,
                "oldContent": old_content,
                "newContent": new_content,
                "modifiedBy": modified_by,
                "summary": summary,
                "createdAt": created_at
            })
    
    return edits_list_template.render(edits=edits)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)