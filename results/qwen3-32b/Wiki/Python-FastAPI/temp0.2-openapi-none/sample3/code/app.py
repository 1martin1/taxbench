from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional
import sqlite3
import uuid
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

@app.on_event("startup")
def startup():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            last_modified_by TEXT NOT NULL,
            last_modified_at DATETIME NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL,
            modified_by TEXT NOT NULL,
            summary TEXT NOT NULL,
            old_content TEXT NOT NULL,
            new_content TEXT NOT NULL,
            timestamp DATETIME NOT NULL,
            FOREIGN KEY (entry_id) REFERENCES entries(id)
        )
    """)
    conn.commit()
    conn.close()

@app.get("/entries", response_class=HTMLResponse)
async def list_entries():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM entries")
    entries = cursor.fetchall()
    conn.close()

    html = "<html><body><h1>Wiki Entries</h1><ul>"
    for entry_id, title in entries:
        html += f"<li><a href='/entries/{entry_id}'>{title}</a></li>"
    html += "</ul></body></html>"
    return html

@app.post("/entries", response_model=Entry, status_code=201)
async def create_entry(entry: NewEntry):
    entry_id = str(uuid.uuid4())
    current_time = datetime.now().isoformat()
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
        VALUES (?, ?, ?, ?, ?)
    """, (entry_id, entry.title, entry.content, entry.createdBy, current_time))
    cursor.execute("""
        INSERT INTO edits (entry_id, modified_by, summary, old_content, new_content, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (entry_id, entry.createdBy, "Initial creation", "", entry.content, current_time))
    conn.commit()
    conn.close()

    return Entry(
        id=entry_id,
        title=entry.title,
        content=entry.content,
        lastModifiedBy=entry.createdBy,
        lastModifiedAt=current_time
    )

@app.get("/entries/{entryId}", response_class=HTMLResponse)
async def get_entry(entryId: str):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT title, content, last_modified_by, last_modified_at FROM entries WHERE id = ?", (entryId,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")

    title, content, last_modified_by, last_modified_at = row
    html = f"<html><body><h1>{title}</h1><p>{content}</p><p>Last modified by {last_modified_by} at {last_modified_at}</p></body></html>"
    return html

@app.put("/entries/{entryId}", response_model=Entry)
async def update_entry(entryId: str, update_data: UpdateEntry):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT title, content, last_modified_by, last_modified_at FROM entries WHERE id = ?", (entryId,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Entry not found")

    title, old_content, last_modified_by, last_modified_at = row
    new_content = update_data.content
    modified_by = update_data.modifiedBy
    current_time = datetime.now().isoformat()

    cursor.execute("""
        UPDATE entries
        SET content = ?, last_modified_by = ?, last_modified_at = ?
        WHERE id = ?
    """, (new_content, modified_by, current_time, entryId))

    cursor.execute("""
        INSERT INTO edits (entry_id, modified_by, summary, old_content, new_content, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (entryId, modified_by, update_data.summary, old_content, new_content, current_time))
    conn.commit()
    conn.close()

    return Entry(
        id=entryId,
        title=title,
        content=new_content,
        lastModifiedBy=modified_by,
        lastModifiedAt=current_time
    )

@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
async def get_edits(entryId: str):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT title FROM entries WHERE id = ?", (entryId,))
    entry_row = cursor.fetchone()
    if not entry_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Entry not found")

    cursor.execute("""
        SELECT modified_by, summary, old_content, new_content, timestamp
        FROM edits
        WHERE entry_id = ?
        ORDER BY timestamp DESC
    """, (entryId,))
    edits = cursor.fetchall()
    conn.close()

    html = f"<html><body><h1>Edit History for {entry_row[0]}</h1><ul>"
    for edit in edits:
        modified_by, summary, old_content, new_content, timestamp = edit
        diff = ''.join(difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile='before',
            tofile='after',
            lineterm=''
        ))
        html += f"""
            <li>
                <p><strong>{modified_by}</strong> - {timestamp}</p>
                <p>{summary}</p>
                <pre>{diff}</pre>
            </li>
        """
    html += "</ul></body></html>"
    return html

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)