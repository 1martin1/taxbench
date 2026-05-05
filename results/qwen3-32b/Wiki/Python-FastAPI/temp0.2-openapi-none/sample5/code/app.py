import sqlite3
import uuid
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from datetime import datetime
import difflib
from fastapi.responses import HTMLResponse
import html

app = FastAPI()

def init_db():
    try:
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
                    old_content TEXT NOT NULL,
                    new_content TEXT NOT NULL,
                    modified_by TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (entry_id) REFERENCES entries (id)
                )
            ''')
            conn.commit()
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database initialization error")

app.add_event_handler("startup", init_db)

class EntryModel(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str
    lastModifiedAt: str

class NewEntryModel(BaseModel):
    title: str
    content: str
    createdBy: str

class UpdateEntryModel(BaseModel):
    content: str
    modifiedBy: str
    summary: str

def generate_diff(old_text, new_text):
    diff = difflib.ndiff(old_text.splitlines(), new_text.splitlines())
    html_lines = []
    for line in diff:
        if line.startswith('+ '):
            html_lines.append(f"<ins>{html.escape(line[2:])}</ins>")
        elif line.startswith('- '):
            html_lines.append(f"<del>{html.escape(line[2:])}</del>")
        elif line.startswith('? '):
            continue
        else:
            html_lines.append(html.escape(line))
    return "<br>".join(html_lines)

@app.get("/entries", response_class=HTMLResponse)
async def get_entries():
    try:
        with sqlite3.connect('db.sqlite3') as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT id, title FROM entries")
            entries = cursor.fetchall()
            html = "<html><body><h1>Wiki Entries</h1><ul>"
            for entry in entries:
                html += f"<li><a href='/entries/{entry['id']}'>{html.escape(entry['title'])}</a></li>"
            html += "</ul></body></html>"
            return html
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database error")

@app.post("/entries", response_model=EntryModel, status_code=201)
async def create_entry(entry: NewEntryModel):
    try:
        entry_id = str(uuid.uuid4())
        current_time = datetime.utcnow().isoformat()
        with sqlite3.connect('db.sqlite3') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO entries (id, title, content, created_by, last_modified_by, last_modified_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (entry_id, entry.title, entry.content, entry.createdBy, entry.createdBy, current_time))
            conn.commit()
        return EntryModel(
            id=entry_id,
            title=entry.title,
            content=entry.content,
            lastModifiedBy=entry.createdBy,
            lastModifiedAt=current_time
        )
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database error")

@app.get("/entries/{entryId}", response_class=HTMLResponse)
async def get_entry(entryId: str):
    try:
        with sqlite3.connect('db.sqlite3') as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM entries WHERE id = ?", (entryId,))
            entry_row = cursor.fetchone()
            if not entry_row:
                raise HTTPException(status_code=404, detail="Entry not found")
            cursor.execute('''
                SELECT DISTINCT modified_by FROM edits WHERE entry_id = ?
                UNION
                SELECT created_by FROM entries WHERE id = ?
            ''', (entryId, entryId))
            contributors = [html.escape(row['modified_by']) for row in cursor.fetchall()]
            html = f"""
            <html>
            <body>
            <h1>{html.escape(entry_row['title'])}</h1>
            <div>{html.escape(entry_row['content'])}</div>
            <p>Last modified by: {html.escape(entry_row['last_modified_by'])} at {entry_row['last_modified_at']}</p>
            <h2>Contributors</h2>
            <ul>
            {''.join(f"<li>{contributor}</li>" for contributor in contributors)}
            </ul>
            <a href="/entries/{entryId}/edits">View edits</a>
            </body>
            </html>
            """
            return html
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database error")

@app.put("/entries/{entryId}", response_model=EntryModel)
async def update_entry(entryId: str, update_data: UpdateEntryModel):
    try:
        with sqlite3.connect('db.sqlite3') as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM entries WHERE id = ?", (entryId,))
            entry_row = cursor.fetchone()
            if not entry_row:
                raise HTTPException(status_code=404, detail="Entry not found")
            current_content = entry_row['content']
            current_time = datetime.utcnow().isoformat()
            cursor.execute('''
                INSERT INTO edits (entry_id, old_content, new_content, modified_by, summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (entryId, current_content, update_data.content, update_data.modifiedBy, update_data.summary, current_time))
            cursor.execute('''
                UPDATE entries
                SET content = ?, last_modified_by = ?, last_modified_at = ?
                WHERE id = ?
            ''', (update_data.content, update_data.modifiedBy, current_time, entryId))
            conn.commit()
        return EntryModel(
            id=entryId,
            title=entry_row['title'],
            content=update_data.content,
            lastModifiedBy=update_data.modifiedBy,
            lastModifiedAt=current_time
        )
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database error")

@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
async def get_edits(entryId: str):
    try:
        with sqlite3.connect('db.sqlite3') as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM entries WHERE id = ?", (entryId,))
            entry_row = cursor.fetchone()
            if not entry_row:
                raise HTTPException(status_code=404, detail="Entry not found")
            cursor.execute("SELECT * FROM edits WHERE entry_id = ? ORDER BY created_at DESC", (entryId,))
            edits = cursor.fetchall()
            html = f"""
            <html>
            <body>
            <h1>Edits for {html.escape(entry_row['title'])}</h1>
            <ul>
            """
            for edit in edits:
                diff_html = generate_diff(edit['old_content'], edit['new_content'])
                html += f"""
                <li>
                    <h3>Edit by {html.escape(edit['modified_by'])} at {edit['created_at']}</h3>
                    <p>{html.escape(edit['summary'])}</p>
                    <div>{diff_html}</div>
                </li>
                """
            html += "</ul></body></html>"
            return html
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)