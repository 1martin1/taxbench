from fastapi import FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
import sqlite3
import markdown_it
import difflib
from datetime import datetime
import uuid
import json
from jinja2 import escape as jinja2_escape

app = FastAPI()

# Initialize database
def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            last_modified_by TEXT NOT NULL,
            last_modified_at TEXT NOT NULL,
            contributors TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL,
            content TEXT NOT NULL,
            modified_by TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (entry_id) REFERENCES entries (id)
        )
    ''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

# Pydantic models
class NewEntry(BaseModel):
    title: str = Field(max_length=255)
    content: str = Field(max_length=100000)
    createdBy: str

class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str
    lastModifiedAt: str

class UpdateEntry(BaseModel):
    content: str = Field(max_length=100000)
    modifiedBy: str
    summary: str = Field(max_length=1000)

# Endpoints
@app.get("/entries", response_class=HTMLResponse)
async def list_entries():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT id, title FROM entries")
    entries = c.fetchall()
    conn.close()

    html = "<html><body><h1>Wiki Entries</h1><ul>"
    for entry_id, title in entries:
        html += f"<li><a href='/entries/{jinja2_escape(entry_id)}'>{jinja2_escape(title)}</a></li>"
    html += "</ul></body></html>"
    return html

@app.post("/entries", response_model=Entry, status_code=status.HTTP_201_CREATED)
async def create_entry(entry: NewEntry):
    entry_id = str(uuid.uuid4())
    current_time = datetime.utcnow().isoformat()
    contributors = json.dumps([entry.createdBy])

    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("INSERT INTO entries (id, title, content, last_modified_by, last_modified_at, contributors) VALUES (?, ?, ?, ?, ?, ?)",
              (entry_id, entry.title, entry.content, entry.createdBy, current_time, contributors))
    c.execute("INSERT INTO edits (entry_id, content, modified_by, summary, created_at) VALUES (?, ?, ?, ?, ?)",
              (entry_id, entry.content, entry.createdBy, "Initial creation", current_time))
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
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT title, content, last_modified_by, last_modified_at, contributors FROM entries WHERE id = ?", (entryId,))
    row = c.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")

    title, content, last_modified_by, last_modified_at, contributors = row
    contributors_list = json.loads(contributors)

    md = markdown_it.MarkdownIt(html=False)
    html_content = md.render(content)

    contributors_html = ', '.join([jinja2_escape(c) for c in contributors_list])

    full_html = f"""
    <html>
        <head><title>{jinja2_escape(title)}</title></head>
        <body>
            <h1>{jinja2_escape(title)}</h1>
            <div>{html_content}</div>
            <p>Last modified by {jinja2_escape(last_modified_by)} at {last_modified_at}</p>
            <p>Contributors: {contributors_html}</p>
        </body>
    </html>
    """
    return full_html

@app.put("/entries/{entryId}", response_model=Entry)
async def update_entry(entryId: str, update: UpdateEntry):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT * FROM entries WHERE id = ?", (entryId,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Entry not found")

    current_time = datetime.utcnow().isoformat()
    existing_contributors = json.loads(row[5])
    if update.modifiedBy not in existing_contributors:
        existing_contributors.append(update.modifiedBy)
    new_contributors = json.dumps(existing_contributors)

    c.execute("UPDATE entries SET content = ?, last_modified_by = ?, last_modified_at = ?, contributors = ? WHERE id = ?",
              (update.content, update.modifiedBy, current_time, new_contributors, entryId))
    c.execute("INSERT INTO edits (entry_id, content, modified_by, summary, created_at) VALUES (?, ?, ?, ?, ?)",
              (entryId, update.content, update.modifiedBy, update.summary, current_time))
    conn.commit()
    conn.close()

    return Entry(
        id=entryId,
        title=row[1],
        content=update.content,
        lastModifiedBy=update.modifiedBy,
        lastModifiedAt=current_time
    )

@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
async def get_edits(entryId: str):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT * FROM entries WHERE id = ?", (entryId,))
    entry_row = c.fetchone()
    if not entry_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Entry not found")

    c.execute("SELECT * FROM edits WHERE entry_id = ? ORDER BY created_at", (entryId,))
    edits = c.fetchall()
    conn.close()

    if not edits:
        return "<html><body><h1>No edits found</h1></body></html>"

    html = "<html><body><h1>Edit History</h1>"
    for i, edit in enumerate(edits):
        edit_id, entry_id, content, modified_by, summary, created_at = edit
        if i == 0:
            diff_html = "<p>No previous version. Initial content.</p>"
        else:
            prev_content = edits[i-1][2]
            diff = difflib.ndiff(prev_content.splitlines(), content.splitlines())
            diff_lines = [line for line in diff if line.startswith('+ ') or line.startswith('- ')]
            diff_html = "<pre>"
            for line in diff_lines:
                if line.startswith('+ '):
                    diff_html += f"<span style='color:green;'>{jinja2_escape(line)}</span>"
                elif line.startswith('- '):
                    diff_html += f"<span style='color:red;'>{jinja2_escape(line)}</span>"
                else:
                    diff_html += jinja2_escape(line)
            diff_html += "</pre>"

        html += f"""
        <div style='border:1px solid #ccc; padding:10px; margin:10px 0;'>
            <h3>Edit by {jinja2_escape(modified_by)} at {created_at}</h3>
            <p><strong>Summary:</strong> {jinja2_escape(summary)}</p>
            {diff_html}
        </div>
        """
    html += "</body></html>"
    return html

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)