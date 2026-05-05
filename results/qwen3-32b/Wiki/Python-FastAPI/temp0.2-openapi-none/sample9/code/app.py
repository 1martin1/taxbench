from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
import sqlite3
import uuid
from datetime import datetime
import difflib
import uvicorn

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
            modified_by TEXT NOT NULL,
            summary TEXT NOT NULL,
            old_content TEXT NOT NULL,
            new_content TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (entry_id) REFERENCES entries (id)
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

# Helper functions
def get_db_connection():
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    return conn

@app.get("/entries", response_class=Response)
def list_entries():
    conn = get_db_connection()
    entries = conn.execute('SELECT id, title FROM entries').fetchall()
    conn.close()
    html = "<h1>Wiki Entries</h1><ul>"
    for entry in entries:
        html += f"<li><a href='/entries/{entry['id']}'>{entry['title']}</a></li>"
    html += "</ul>"
    return Response(content=html, media_type="text/html")

@app.post("/entries", response_model=Entry, status_code=201)
def create_entry(entry_data: NewEntry):
    entry_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (entry_id, entry_data.title, entry_data.content, entry_data.createdBy, timestamp))
    conn.commit()
    entry = conn.execute('SELECT * FROM entries WHERE id = ?', (entry_id,)).fetchone()
    conn.close()
    return Entry(**entry)

@app.get("/entries/{entryId}", response_class=Response)
def get_entry(entryId: str = Path(..., description="The ID of the entry")):
    conn = get_db_connection()
    entry = conn.execute('SELECT * FROM entries WHERE id = ?', (entryId,)).fetchone()
    conn.close()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    html = f"""
    <h1>{entry['title']}</h1>
    <pre>{entry['content']}</pre>
    <p>Last modified by: {entry['last_modified_by']} at {entry['last_modified_at']}</p>
    <p>Contributors: {entry['last_modified_by']}</p>
    """
    return Response(content=html, media_type="text/html")

@app.put("/entries/{entryId}", response_model=Entry)
def update_entry(entryId: str, update_data: UpdateEntry):
    conn = get_db_connection()
    entry = conn.execute('SELECT * FROM entries WHERE id = ?', (entryId,)).fetchone()
    if not entry:
        conn.close()
        raise HTTPException(status_code=404, detail="Entry not found")
    
    old_content = entry['content']
    timestamp = datetime.now().isoformat()
    
    # Save edit history
    conn.execute('''
        INSERT INTO edits (entry_id, modified_by, summary, old_content, new_content, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (entryId, update_data.modifiedBy, update_data.summary, old_content, update_data.content, timestamp))
    
    # Update entry
    conn.execute('''
        UPDATE entries
        SET content = ?, last_modified_by = ?, last_modified_at = ?
        WHERE id = ?
    ''', (update_data.content, update_data.modifiedBy, timestamp, entryId))
    conn.commit()
    
    updated_entry = conn.execute('SELECT * FROM entries WHERE id = ?', (entryId,)).fetchone()
    conn.close()
    return Entry(**updated_entry)

@app.get("/entries/{entryId}/edits", response_class=Response)
def get_edits(entryId: str = Path(..., description="The ID of the entry")):
    conn = get_db_connection()
    entry = conn.execute('SELECT * FROM entries WHERE id = ?', (entryId,)).fetchone()
    if not entry:
        conn.close()
        raise HTTPException(status_code=404, detail="Entry not found")
    
    edits = conn.execute('SELECT * FROM edits WHERE entry_id = ? ORDER BY timestamp DESC', (entryId,)).fetchall()
    conn.close()
    
    html = "<h1>Edit History</h1>"
    for edit in edits:
        diff = difflib.unified_diff(
            edit['old_content'].splitlines(),
            edit['new_content'].splitlines(),
            fromfile='Previous version',
            tofile='Current version',
            lineterm=''
        )
        diff_html = "<pre>" + "\n".join(diff) + "</pre>"
        html += f"""
        <div style='border:1px solid #ccc; padding:10px; margin:10px 0;'>
            <p><strong>Modified by:</strong> {edit['modified_by']}</p>
            <p><strong>Summary:</strong> {edit['summary']}</p>
            <p><strong>Timestamp:</strong> {edit['timestamp']}</p>
            {diff_html}
        </div>
        """
    return Response(content=html, media_type="text/html")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)