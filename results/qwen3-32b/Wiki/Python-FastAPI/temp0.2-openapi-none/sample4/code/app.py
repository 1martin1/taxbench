import sqlite3
from fastapi import FastAPI, HTTPException, HTMLResponse
from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional

app = FastAPI()

# Initialize the database
def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            last_modified_by TEXT NOT NULL,
            last_modified_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL,
            modified_by TEXT NOT NULL,
            summary TEXT NOT NULL,
            old_content TEXT NOT NULL,
            new_content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (entry_id) REFERENCES entries (id)
        )
    ''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

# Pydantic models
class Entry(BaseModel):
    id: int
    title: str
    content: str
    last_modified_by: str
    last_modified_at: datetime

class NewEntry(BaseModel):
    title: str
    content: str
    created_by: str

class UpdateEntry(BaseModel):
    content: str
    modified_by: str
    summary: str

class Edit(BaseModel):
    id: int
    entry_id: int
    modified_by: str
    summary: str
    old_content: str
    new_content: str
    timestamp: datetime

@app.get("/entries")
async def list_entries():
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT id, title FROM entries")
    rows = c.fetchall()
    conn.close()
    html = "<html><body><h1>Wiki Entries</h1><ul>"
    for row in rows:
        html += f"<li><a href='/entries/{row['id']}'>{row['title']}</a></li>"
    html += "</ul></body></html>"
    return HTMLResponse(content=html, status_code=200)

@app.post("/entries", response_model=Entry, status_code=201)
async def create_entry(entry: NewEntry):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        INSERT INTO entries (title, content, last_modified_by, last_modified_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    ''', (entry.title, entry.content, entry.created_by))
    entry_id = c.lastrowid
    conn.commit()
    conn.close()
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
    created_entry = c.fetchone()
    conn.close()
    return Entry(**created_entry)

@app.get("/entries/{entryId}", response_class=HTMLResponse)
async def read_entry(entryId: int):
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM entries WHERE id = ?", (entryId,))
    entry = c.fetchone()
    conn.close()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT modified_by FROM edits WHERE entry_id = ?", (entryId,))
    contributors = c.fetchall()
    contributors_list = list(set(row['modified_by'] for row in contributors))
    conn.close()
    html_content = f"""
    <html>
    <body>
        <h1>{entry['title']}</h1>
        <div>{entry['content']}</div>
        <p>Last modified by: {entry['last_modified_by']} at {entry['last_modified_at']}</p>
        <h2>Contributors</h2>
        <ul>
            {''.join(f"<li>{contributor}</li>" for contributor in contributors_list)}
        </ul>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)

@app.put("/entries/{entryId}", response_model=Entry)
async def update_entry(entryId: int, update_data: UpdateEntry):
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM entries WHERE id = ?", (entryId,))
    existing_entry = c.fetchone()
    if not existing_entry:
        conn.close()
        raise HTTPException(status_code=404, detail="Entry not found")
    c.execute('''
        INSERT INTO edits (entry_id, modified_by, summary, old_content, new_content)
        VALUES (?, ?, ?, ?, ?)
    ''', (entryId, update_data.modified_by, update_data.summary, existing_entry['content'], update_data.content))
    c.execute('''
        UPDATE entries
        SET content = ?, last_modified_by = ?, last_modified_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (update_data.content, update_data.modified_by, entryId))
    conn.commit()
    c.execute("SELECT * FROM entries WHERE id = ?", (entryId,))
    updated_entry = c.fetchone()
    conn.close()
    return Entry(**updated_entry)

@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
async def list_edits(entryId: int):
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM edits WHERE entry_id = ? ORDER BY timestamp DESC", (entryId,))
    edits = c.fetchall()
    conn.close()
    if not edits:
        raise HTTPException(status_code=404, detail="Entry not found")
    html = "<html><body><h1>Edit History</h1><ul>"
    for edit in edits:
        html += f"""
        <li>
            <p><strong>{edit['modified_by']}</strong> - {edit['timestamp']}</p>
            <p><em>{edit['summary']}</em></p>
            <div><strong>Old:</strong> {edit['old_content']}</div>
            <div><strong>New:</strong> {edit['new_content']}</div>
            <hr>
        </li>
        """
    html += "</ul></body></html>"
    return HTMLResponse(content=html, status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)