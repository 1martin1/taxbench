import sqlite3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.responses import HTMLResponse
from typing import List, Optional, Dict, Any
import uuid
import datetime
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

def init_db():
    with sqlite3.connect('db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_by TEXT NOT NULL,
                last_modified_by TEXT NOT NULL,
                last_modified_at TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS edits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                modified_by TEXT NOT NULL,
                summary TEXT NOT NULL,
                old_content TEXT NOT NULL,
                new_content TEXT NOT NULL,
                modified_at TEXT NOT NULL,
                FOREIGN KEY (entry_id) REFERENCES entries(id)
            )
        ''')
        conn.commit()

@app.on_event("startup")
def startup_event():
    init_db()

@app.get("/entries", response_class=HTMLResponse)
async def list_entries():
    with sqlite3.connect('db.sqlite3') as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id, title FROM entries")
        entries = cursor.fetchall()
        if not entries:
            return "<html><body>No entries found.</body></html>"
        html = "<html><body><h1>Wiki Entries</h1><ul>"
        for entry in entries:
            html += f"<li><a href='/entries/{entry['id']}'>{entry['title']}</a></li>"
        html += "</ul></body></html>"
        return HTMLResponse(content=html)

@app.post("/entries", response_model=Entry)
async def create_entry(entry_data: NewEntry):
    entry_id = str(uuid.uuid4().hex)
    current_time = datetime.datetime.now().isoformat()
    with sqlite3.connect('db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO entries (id, title, content, created_by, last_modified_by, last_modified_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (entry_id, entry_data.title, entry_data.content, entry_data.createdBy, entry_data.createdBy, current_time))
        conn.commit()
        cursor.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
        created_entry = cursor.fetchone()
        return Entry(
            id=created_entry[0],
            title=created_entry[1],
            content=created_entry[2],
            lastModifiedBy=created_entry[4],
            lastModifiedAt=created_entry[5]
        )

@app.get("/entries/{entryId}", response_class=HTMLResponse)
async def get_entry(entryId: str):
    with sqlite3.connect('db.sqlite3') as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM entries WHERE id = ?", (entryId,))
        entry = cursor.fetchone()
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        cursor.execute('''
            SELECT created_by AS name FROM entries WHERE id = ?
            UNION
            SELECT modified_by AS name FROM edits WHERE entry_id = ?
        ''', (entryId, entryId))
        contributors = [row['name'] for row in cursor.fetchall()]
        html = f"""
        <html>
        <body>
            <h1>{entry['title']}</h1>
            <p>{entry['content']}</p>
            <p>Last modified by: {entry['last_modified_by']} at {entry['last_modified_at']}</p>
            <h2>Contributors</h2>
            <ul>
        """
        for contributor in contributors:
            html += f"<li>{contributor}</li>"
        html += """
            </ul>
        </body>
        </html>
        """
        return HTMLResponse(content=html)

@app.put("/entries/{entryId}", response_model=Entry)
async def update_entry(entryId: str, update_data: UpdateEntry):
    with sqlite3.connect('db.sqlite3') as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM entries WHERE id = ?", (entryId,))
        entry = cursor.fetchone()
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        old_content = entry['content']
        cursor.execute("SELECT MAX(version) AS max_version FROM edits WHERE entry_id = ?", (entryId,))
        max_version_row = cursor.fetchone()
        current_version = max_version_row['max_version'] if max_version_row and max_version_row['max_version'] is not None else 0
        new_version = current_version + 1 if current_version is not None else 1
        modified_at = datetime.datetime.now().isoformat()
        cursor.execute('''
            INSERT INTO edits (entry_id, version, modified_by, summary, old_content, new_content, modified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (entryId, new_version, update_data.modifiedBy, update_data.summary, old_content, update_data.content, modified_at))
        cursor.execute('''
            UPDATE entries
            SET content = ?, last_modified_by = ?, last_modified_at = ?
            WHERE id = ?
        ''', (update_data.content, update_data.modifiedBy, modified_at, entryId))
        conn.commit()
        cursor.execute("SELECT * FROM entries WHERE id = ?", (entryId,))
        updated_entry = cursor.fetchone()
        return Entry(
            id=updated_entry["id"],
            title=updated_entry["title"],
            content=updated_entry["content"],
            lastModifiedBy=updated_entry["last_modified_by"],
            lastModifiedAt=updated_entry["last_modified_at"]
        )

@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
async def get_edits(entryId: str):
    with sqlite3.connect('db.sqlite3') as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM entries WHERE id = ?", (entryId,))
        entry = cursor.fetchone()
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        cursor.execute("SELECT * FROM edits WHERE entry_id = ? ORDER BY modified_at DESC", (entryId,))
        edits = cursor.fetchall()
        if not edits:
            return "<html><body>No edits found.</body></html>"
        html = "<html><body><h1>Edit History</h1>"
        for edit in edits:
            diff = difflib.unified_diff(
                edit['old_content'].splitlines(),
                edit['new_content'].splitlines(),
                fromfile='Previous',
                tofile='Current',
                lineterm=''
            )
            diff_text = '\n'.join(diff)
            html += f"""
                <h2>Edit by {edit['modified_by']} - {edit['summary']}</h2>
                <pre>{diff_text}</pre>
                <hr>
            """
        html += "</body></html>"
        return HTMLResponse(content=html)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)