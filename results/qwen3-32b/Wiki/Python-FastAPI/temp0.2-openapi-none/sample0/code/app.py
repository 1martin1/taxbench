from fastapi import FastAPI, HTTPException, Path
from fastapi.responses import HTMLResponse
import sqlite3
import uuid
from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional

app = FastAPI()

def get_db():
    db = sqlite3.connect('db.sqlite3')
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with get_db() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS entries (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        last_modified_by TEXT NOT NULL,
                        last_modified_at TEXT NOT NULL
                    )''')
        db.execute('''CREATE TABLE IF NOT EXISTS edits (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        entry_id TEXT NOT NULL,
                        content TEXT NOT NULL,
                        modified_by TEXT NOT NULL,
                        summary TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(entry_id) REFERENCES entries(id)
                    )''')
        db.commit()

init_db()

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
    summary: Optional[str] = None

@app.get("/entries", response_class=HTMLResponse)
async def list_entries():
    db = get_db()
    entries = db.execute("SELECT id, title FROM entries").fetchall()
    db.close()
    html = "<html><body><h1>Entries</h1><ul>"
    for entry in entries:
        html += f"<li><a href='/entries/{entry['id']}'>{entry['title']}</a></li>"
    html += "</ul></body></html>"
    return html

@app.post("/entries", response_model=Entry, status_code=201)
async def create_entry(entry: NewEntry):
    entry_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO entries (id, title, content, last_modified_by, last_modified_at) VALUES (?, ?, ?, ?, ?)",
        (entry_id, entry.title, entry.content, entry.createdBy, now)
    )
    db.execute(
        "INSERT INTO edits (entry_id, content, modified_by, summary, created_at) VALUES (?, ?, ?, ?, ?)",
        (entry_id, entry.content, entry.createdBy, "Initial creation", now)
    )
    db.commit()
    db.close()
    return Entry(
        id=entry_id,
        title=entry.title,
        content=entry.content,
        lastModifiedBy=entry.createdBy,
        lastModifiedAt=now
    )

@app.get("/entries/{entryId}", response_class=HTMLResponse)
async def get_entry(entryId: str = Path(..., title="The ID of the entry")):
    db = get_db()
    entry = db.execute("SELECT * FROM entries WHERE id = ?", (entryId,)).fetchone()
    if not entry:
        db.close()
        raise HTTPException(status_code=404, detail="Entry not found")
    contributors = db.execute("SELECT DISTINCT modified_by FROM edits WHERE entry_id = ?", (entryId,)).fetchall()
    contributors = [c['modified_by'] for c in contributors]
    db.close()
    html = f"""
    <html>
    <body>
        <h1>{entry['title']}</h1>
        <p>{entry['content']}</p>
        <p>Last modified by: {entry['last_modified_by']} at {entry['last_modified_at']}</p>
        <h2>Contributors</h2>
        <ul>
            {''.join(f"<li>{c}</li>" for c in contributors)}
        </ul>
    </body>
    </html>
    """
    return html

@app.put("/entries/{entryId}", response_model=Entry)
async def update_entry(entryId: str, update: UpdateEntry):
    db = get_db()
    entry = db.execute("SELECT * FROM entries WHERE id = ?", (entryId,)).fetchone()
    if not entry:
        db.close()
        raise HTTPException(status_code=404, detail="Entry not found")
    now = datetime.now().isoformat()
    db.execute(
        "UPDATE entries SET content = ?, last_modified_by = ?, last_modified_at = ? WHERE id = ?",
        (update.content, update.modifiedBy, now, entryId)
    )
    db.execute(
        "INSERT INTO edits (entry_id, content, modified_by, summary, created_at) VALUES (?, ?, ?, ?, ?)",
        (entryId, update.content, update.modifiedBy, update.summary, now)
    )
    db.commit()
    db.close()
    return Entry(
        id=entryId,
        title=entry['title'],
        content=update.content,
        lastModifiedBy=update.modifiedBy,
        lastModifiedAt=now
    )

@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
async def get_edits(entryId: str):
    db = get_db()
    entry = db.execute("SELECT * FROM entries WHERE id = ?", (entryId,)).fetchone()
    if not entry:
        db.close()
        raise HTTPException(status_code=404, detail="Entry not found")
    edits = db.execute("SELECT * FROM edits WHERE entry_id = ? ORDER BY created_at DESC", (entryId,)).fetchall()
    db.close()
    html = "<html><body><h1>Edit History</h1><ul>"
    for edit in edits:
        html += f"""
        <li>
            <p><strong>Modified by:</strong> {edit['modified_by']}</p>
            <p><strong>Summary:</strong> {edit['summary'] or 'No summary'}</p>
            <p><strong>Content:</strong> {edit['content']}</p>
            <p><strong>Created at:</strong> {edit['created_at']}</p>
            <hr>
        </li>
        """
    html += "</ul></body></html>"
    return html

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)