import uuid
import datetime
import sqlite3
import difflib
import html
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, StringConstraints
from typing import List, Optional, Annotated
from typing_extensions import Annotated

app = FastAPI()

def get_db():
    db = sqlite3.connect('db.sqlite3', check_same_thread=False)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()

@app.on_event("startup")
def create_tables():
    db = sqlite3.connect('db.sqlite3')
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            last_modified_by TEXT NOT NULL,
            last_modified_at DATETIME NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS edits (
            entry_id TEXT NOT NULL,
            content TEXT NOT NULL,
            modified_by TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE
        )
    """)
    db.commit()
    db.close()

class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str
    lastModifiedAt: str

class NewEntry(BaseModel):
    title: Annotated[str, StringConstraints(max_length=255)]
    content: Annotated[str, StringConstraints(max_length=10000)]
    createdBy: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_\-\. ]+$", max_length=255)]

class UpdateEntry(BaseModel):
    content: Annotated[str, StringConstraints(max_length=10000)]
    modifiedBy: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_\-\. ]+$", max_length=255)]
    summary: Annotated[str, StringConstraints(max_length=500)]

@app.get("/entries")
def list_entries(db: sqlite3.Connection = Depends(get_db)):
    entries = db.execute("SELECT id, title FROM entries").fetchall()
    html_parts = [
        "<html><body><h1>Wiki Entries</h1><ul>"
    ]
    for entry in entries:
        html_parts.append(
            f"<li><a href='/entries/{html.escape(entry['id'])}'>{html.escape(entry['title'])}</a></li>"
        )
    html_parts.append("</ul></body></html>")
    return HTMLResponse(content=''.join(html_parts), status_code=200)

@app.post("/entries", response_model=Entry, status_code=201)
def create_entry(entry_data: NewEntry, db: sqlite3.Connection = Depends(get_db)):
    entry_id = str(uuid.uuid4())
    current_time = datetime.datetime.now().isoformat()
    db.execute("""
        INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
        VALUES (?, ?, ?, ?, ?)
    """, (entry_id, entry_data.title, entry_data.content, entry_data.createdBy, current_time))
    db.execute("""
        INSERT INTO edits (entry_id, content, modified_by, summary, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (entry_id, entry_data.content, entry_data.createdBy, "Initial creation", current_time))
    db.commit()
    entry = db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return Entry(
        id=entry['id'],
        title=entry['title'],
        content=entry['content'],
        lastModifiedBy=entry['last_modified_by'],
        lastModifiedAt=entry['last_modified_at']
    )

@app.get("/entries/{entryId}")
def get_entry(entryId: str, db: sqlite3.Connection = Depends(get_db)):
    entry = db.execute("SELECT * FROM entries WHERE id = ?", (entryId,)).fetchone()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    contributors = db.execute("""
        SELECT DISTINCT modified_by FROM edits WHERE entry_id = ?
    """, (entryId,)).fetchall()
    contributors_list = ', '.join([row['modified_by'] for row in contributors])
    html_content = f"""
    <html>
    <head><title>{html.escape(entry['title'])}</title></head>
    <body>
    <h1>{html.escape(entry['title'])}</h1>
    <div>{html.escape(entry['content'])}</div>
    <p>Last modified by: {html.escape(entry['last_modified_by'])} on {entry['last_modified_at']}</p>
    <p>Contributors: {html.escape(contributors_list)}</p>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)

@app.put("/entries/{entryId}", response_model=Entry)
def update_entry(entryId: str, update_data: UpdateEntry, db: sqlite3.Connection = Depends(get_db)):
    entry = db.execute("SELECT * FROM entries WHERE id = ?", (entryId,)).fetchone()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    current_time = datetime.datetime.now().isoformat()
    db.execute("""
        UPDATE entries
        SET content = ?, last_modified_by = ?, last_modified_at = ?
        WHERE id = ?
    """, (update_data.content, update_data.modifiedBy, current_time, entryId))
    db.execute("""
        INSERT INTO edits (entry_id, content, modified_by, summary, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (entryId, update_data.content, update_data.modifiedBy, update_data.summary, current_time))
    db.commit()
    updated_entry = db.execute("SELECT * FROM entries WHERE id = ?", (entryId,)).fetchone()
    return Entry(
        id=updated_entry['id'],
        title=updated_entry['title'],
        content=updated_entry['content'],
        lastModifiedBy=updated_entry['last_modified_by'],
        lastModifiedAt=updated_entry['last_modified_at']
    )

@app.get("/entries/{entryId}/edits")
def get_edits(entryId: str, db: sqlite3.Connection = Depends(get_db)):
    entry = db.execute("SELECT * FROM entries WHERE id = ?", (entryId,)).fetchone()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    edits = db.execute("""
        SELECT * FROM edits WHERE entry_id = ? ORDER BY created_at ASC
    """, (entryId,)).fetchall()
    edits_list = [dict(edit) for edit in edits]
    html_parts = [
        "<html><body><h1>Edit History</h1><ul>"
    ]
    prev_content = None
    for edit in edits_list:
        if prev_content is None:
            diff = "Initial content"
        else:
            d = difflib.ndiff(prev_content.splitlines(), edit['content'].splitlines())
            diff_lines = []
            for line in d:
                if line.startswith('+'):
                    diff_lines.append(f"<ins>{html.escape(line[2:])}</ins>")
                elif line.startswith('-'):
                    diff_lines.append(f"<del>{html.escape(line[2:])}</del>")
                else:
                    diff_lines.append(html.escape(line[2:]))
            diff = "<br>".join(diff_lines)
        html_parts.append(
            f"""
            <li>
                <h3>Edit by {html.escape(edit['modified_by'])} on {edit['created_at']}</h3>
                <p>{html.escape(edit['summary'])}</p>
                <pre>{diff}</pre>
            </li>
            """
        )
        prev_content = edit['content']
    html_parts.append("</ul></body></html>")
    return HTMLResponse(content=''.join(html_parts), status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)