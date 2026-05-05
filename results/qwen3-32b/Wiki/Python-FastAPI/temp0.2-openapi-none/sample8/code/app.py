import uuid
import datetime
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
import sqlite3
from pydantic import BaseModel

app = FastAPI()

# Initialize database
def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS entries
                 (id TEXT PRIMARY KEY,
                  title TEXT,
                  content TEXT,
                  last_modified_by TEXT,
                  last_modified_at DATETIME)''')
    c.execute('''CREATE TABLE IF NOT EXISTS edits
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  entry_id TEXT,
                  previous_content TEXT,
                  new_content TEXT,
                  modified_by TEXT,
                  summary TEXT,
                  created_at DATETIME,
                  FOREIGN KEY(entry_id) REFERENCES entries(id))''')
    conn.commit()
    conn.close()

init_db()

# Pydantic models
class NewEntry(BaseModel):
    title: str
    content: str
    createdBy: str

class UpdateEntry(BaseModel):
    content: str
    modifiedBy: str
    summary: str

class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str
    lastModifiedAt: datetime.datetime

@app.get("/entries", response_class=HTMLResponse)
async def get_entries():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT id, title FROM entries")
    entries = c.fetchall()
    conn.close()
    html = "<html><body><h1>Entries</h1><ul>"
    for entry_id, title in entries:
        html += f"<li><a href='/entries/{entry_id}'>{title}</a></li>"
    html += "</ul></body></html>"
    return html

@app.post("/entries", response_model=Entry, status_code=201)
async def create_entry(entry: NewEntry):
    entry_id = str(uuid.uuid4())
    current_time = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
    dt = datetime.datetime.fromisoformat(current_time)
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("INSERT INTO entries (id, title, content, last_modified_by, last_modified_at) VALUES (?, ?, ?, ?, ?)",
              (entry_id, entry.title, entry.content, entry.createdBy, current_time))
    conn.commit()
    conn.close()
    return Entry(id=entry_id, title=entry.title, content=entry.content, lastModifiedBy=entry.createdBy, lastModifiedAt=dt)

@app.get("/entries/{entryId}", response_class=HTMLResponse)
async def get_entry(entryId: str):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT title, content, last_modified_by, last_modified_at FROM entries WHERE id = ?", (entryId,))
    row = c.fetchone()
    if not row:
        conn.close()
        return JSONResponse(status_code=404, content={"detail": "Entry not found"})
    title, content, last_modified_by, last_modified_at = row
    c.execute("SELECT modified_by FROM edits WHERE entry_id = ?", (entryId,))
    contributors = [row[0] for row in c.fetchall()]
    contributors.append(last_modified_by)
    contributors = list(set(contributors))
    conn.close()
    html = f"<html><body><h1>{title}</h1><p>{content}</p>"
    html += f"<p>Last modified by {last_modified_by} on {last_modified_at}</p>"
    html += "<h2>Contributors</h2><ul>"
    for contributor in contributors:
        html += f"<li>{contributor}</li>"
    html += "</ul></body></html>"
    return html

@app.put("/entries/{entryId}", response_model=Entry)
async def update_entry(entryId: str, update: UpdateEntry):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT title, content FROM entries WHERE id = ?", (entryId,))
    row = c.fetchone()
    if not row:
        conn.close()
        return JSONResponse(status_code=404, content={"detail": "Entry not found"})
    title, previous_content = row
    current_time = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
    dt = datetime.datetime.fromisoformat(current_time)
    c.execute("INSERT INTO edits (entry_id, previous_content, new_content, modified_by, summary, created_at) VALUES (?, ?, ?, ?, ?, ?)",
              (entryId, previous_content, update.content, update.modifiedBy, update.summary, current_time))
    c.execute("UPDATE entries SET content = ?, last_modified_by = ?, last_modified_at = ? WHERE id = ?",
              (update.content, update.modifiedBy, current_time, entryId))
    conn.commit()
    conn.close()
    return Entry(id=entryId, title=title, content=update.content, lastModifiedBy=update.modifiedBy, lastModifiedAt=dt)

@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
async def get_edits(entryId: str):
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute("SELECT id, title FROM entries WHERE id = ?", (entryId,))
    entry = c.fetchone()
    if not entry:
        conn.close()
        return JSONResponse(status_code=404, content={"detail": "Entry not found"})
    entry_id_db, title = entry
    c.execute("SELECT previous_content, new_content, modified_by, summary, created_at FROM edits WHERE entry_id = ? ORDER BY created_at DESC", (entryId,))
    edits = c.fetchall()
    conn.close()
    html = f"<html><body><h1>Edits for {title}</h1><ul>"
    for previous, new, modified_by, summary, created_at in edits:
        html += f"<li><p>Contributor: {modified_by}</p><p>Summary: {summary}</p><p>Created at: {created_at}</p>"
        html += f"<h3>Previous Content:</h3><pre>{previous}</pre>"
        html += f"<h3>New Content:</h3><pre>{new}</pre></li>"
    html += "</ul></body></html>"
    return html

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)