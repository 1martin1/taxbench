import sqlite3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uuid
import difflib
from datetime import datetime
import html
from fastapi.responses import HTMLResponse

app = FastAPI()

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
            timestamp TEXT NOT NULL,
            summary TEXT NOT NULL,
            diff TEXT NOT NULL,
            FOREIGN KEY(entry_id) REFERENCES entries(id)
        )
    ''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

def insert_edit(entry_id, modified_by, timestamp, summary, diff):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        INSERT INTO edits (entry_id, modified_by, timestamp, summary, diff)
        VALUES (?, ?, ?, ?, ?)
    ''', (entry_id, modified_by, timestamp, summary, diff))
    conn.commit()
    conn.close()

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

@app.get("/entries", response_class=HTMLResponse)
async def list_entries():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT id, title FROM entries")
    entries = c.fetchall()
    conn.close()
    
    html_content = "<html><body><h1>Wiki Entries</h1><ul>"
    for entry_id, title in entries:
        html_content += f"<li><a href='/entries/{entry_id}'>{html.escape(title)}</a></li>"
    html_content += "</ul></body></html>"
    return HTMLResponse(content=html_content)

@app.post("/entries", response_model=Entry, status_code=201)
async def create_entry(entry: NewEntry):
    entry_id = str(uuid.uuid4())
    current_time = datetime.utcnow().isoformat()
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (entry_id, entry.title, entry.content, entry.createdBy, current_time))
    conn.commit()
    conn.close()
    
    diff = ''.join(difflib.unified_diff([], entry.content.splitlines(), fromfile='old', tofile='new'))
    insert_edit(entry_id, entry.createdBy, current_time, "Initial creation", diff)
    
    return Entry(
        id=entry_id,
        title=entry.title,
        content=entry.content,
        lastModifiedBy=entry.createdBy,
        lastModifiedAt=current_time
    )

@app.get("/entries/{entryId}", response_class=HTMLResponse)
async def get_entry(entryId: str):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT title, content, last_modified_by, last_modified_at FROM entries WHERE id = ?", (entryId,))
    result = c.fetchone()
    conn.close()
    if not result:
        raise HTTPException(status_code=404, detail="Entry not found")
    
    title, content, last_modified_by, last_modified_at = result
    
    html_content = f"""
    <html>
    <body>
        <h1>{html.escape(title)}</h1>
        <p>{html.escape(content)}</p>
        <p>Last modified by: {html.escape(last_modified_by)} at {last_modified_at}</p>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.put("/entries/{entryId}", response_model=Entry)
async def update_entry(entryId: str, update_data: UpdateEntry):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT title, content, last_modified_by, last_modified_at FROM entries WHERE id = ?", (entryId,))
    result = c.fetchone()
    conn.close()
    if not result:
        raise HTTPException(status_code=404, detail="Entry not found")
    
    title, old_content, _, _ = result
    
    diff = ''.join(difflib.unified_diff(
        old_content.splitlines(),
        update_data.content.splitlines(),
        fromfile='old',
        tofile='new'
    ))
    
    current_time = datetime.utcnow().isoformat()
    insert_edit(entryId, update_data.modifiedBy, current_time, update_data.summary, diff)
    
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        UPDATE entries
        SET content = ?, last_modified_by = ?, last_modified_at = ?
        WHERE id = ?
    ''', (update_data.content, update_data.modifiedBy, current_time, entryId))
    conn.commit()
    conn.close()
    
    return Entry(
        id=entryId,
        title=title,
        content=update_data.content,
        lastModifiedBy=update_data.modifiedBy,
        lastModifiedAt=current_time
    )

@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
async def get_edits(entryId: str):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT id, modified_by, timestamp, summary, diff FROM edits WHERE entry_id = ?", (entryId,))
    edits = c.fetchall()
    conn.close()
    
    if not edits:
        raise HTTPException(status_code=404, detail="Entry not found")
    
    html_content = "<html><body><h1>Edit History</h1><ul>"
    for edit_id, modified_by, timestamp, summary, diff in edits:
        html_content += f"""
        <li>
            <h3>Edit by {html.escape(modified_by)} at {timestamp}</h3>
            <p><strong>Summary:</strong> {html.escape(summary)}</p>
            <pre>{html.escape(diff)}</pre>
        </li>
        """
    html_content += "</ul></body></html>"
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)