import sqlite3
import uuid
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from datetime import datetime
import difflib

app = FastAPI()

class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str
    lastModifiedAt: str

class NewEntry(BaseModel):
    title: str
    content: str
    createdBy: str

class UpdateEntry(BaseModel):
    content: str
    modifiedBy: str
    summary: str

def get_db():
    db = sqlite3.connect('db.sqlite3')
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with get_db() as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                last_modified_by TEXT NOT NULL,
                last_modified_at TEXT NOT NULL
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS edits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT NOT NULL,
                previous_content TEXT NOT NULL,
                new_content TEXT NOT NULL,
                modified_by TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (entry_id) REFERENCES entries(id)
            )
        ''')
        db.commit()

@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/entries")
def list_entries():
    db = get_db()
    entries = db.execute("SELECT * FROM entries").fetchall()
    db.close()
    html = "<html><body><h1>Wiki Entries</h1><ul>"
    for entry in entries:
        html += f"<li><a href='/entries/{entry['id']}'>{entry['title']}</a></li>"
    html += "</ul></body></html>"
    return Response(content=html, media_type="text/html")

@app.post("/entries", status_code=201)
def create_entry(entry: NewEntry):
    entry_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db = get_db()
    db.execute('''
        INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (entry_id, entry.title, entry.content, entry.createdBy, now))
    db.commit()
    db.close()
    return Entry(
        id=entry_id,
        title=entry.title,
        content=entry.content,
        lastModifiedBy=entry.createdBy,
        lastModifiedAt=now
    )

@app.get("/entries/{entryId}")
def get_entry(entryId: str):
    db = get_db()
    entry = db.execute("SELECT * FROM entries WHERE id = ?", (entryId,)).fetchone()
    if not entry:
        db.close()
        raise HTTPException(status_code=404, detail="Entry not found")
    
    contributors = set()
    contributors.add(entry['last_modified_by'])
    edits = db.execute("SELECT modified_by FROM edits WHERE entry_id = ?", (entryId,)).fetchall()
    for edit in edits:
        contributors.add(edit['modified_by'])
    contributors = list(contributors)
    
    db.close()
    
    html = f"<html><body><h1>{entry['title']}</h1><p>{entry['content']}</p>"
    html += f"<p>Last modified by: {entry['last_modified_by']} at {entry['last_modified_at']}</p>"
    html += "<h2>Contributors:</h2><ul>"
    for contributor in contributors:
        html += f"<li>{contributor}</li>"
    html += "</ul></body></html>"
    return Response(content=html, media_type="text/html")

@app.put("/entries/{entryId}", response_model=Entry)
def update_entry(entryId: str, update_data: UpdateEntry):
    db = get_db()
    entry = db.execute("SELECT * FROM entries WHERE id = ?", (entryId,)).fetchone()
    if not entry:
        db.close()
        raise HTTPException(status_code=404, detail="Entry not found")
    
    now = datetime.now().isoformat()
    db.execute('''
        INSERT INTO edits (entry_id, previous_content, new_content, modified_by, summary, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (entryId, entry['content'], update_data.content, update_data.modifiedBy, update_data.summary, now))
    
    db.execute('''
        UPDATE entries
        SET content = ?, last_modified_by = ?, last_modified_at = ?
        WHERE id = ?
    ''', (update_data.content, update_data.modifiedBy, now, entryId))
    
    updated_entry = db.execute("SELECT * FROM entries WHERE id = ?", (entryId,)).fetchone()
    db.commit()
    db.close()
    
    return Entry(
        id=updated_entry['id'],
        title=updated_entry['title'],
        content=updated_entry['content'],
        lastModifiedBy=updated_entry['last_modified_by'],
        lastModifiedAt=updated_entry['last_modified_at']
    )

@app.get("/entries/{entryId}/edits")
def get_edits(entryId: str):
    db = get_db()
    entry = db.execute("SELECT * FROM entries WHERE id = ?", (entryId,)).fetchone()
    if not entry:
        db.close()
        raise HTTPException(status_code=404, detail="Entry not found")
    
    edits = db.execute("SELECT * FROM edits WHERE entry_id = ? ORDER BY created_at DESC", (entryId,)).fetchall()
    db.close()
    
    html = "<html><body><h1>Edit History</h1><ul>"
    for edit in edits:
        diff = difflib.unified_diff(
            edit['previous_content'].splitlines(),
            edit['new_content'].splitlines(),
            fromfile='Previous',
            tofile='New',
            lineterm=''
        )
        diff_str = '\n'.join(diff)
        
        html += f"<li><h3>{edit['summary']}</h3><p>By: {edit['modified_by']}</p><pre>{diff_str}</pre></li>"
    html += "</ul></body></html>"
    return Response(content=html, media_type="text/html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)