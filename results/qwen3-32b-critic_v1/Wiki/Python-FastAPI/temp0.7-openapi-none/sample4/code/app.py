from fastapi import FastAPI, HTTPException, Path, Request
from pydantic import BaseModel, Field
import sqlite3
import uuid
from datetime import datetime
import difflib
import html
from typing import List, Optional
from fastapi.middleware import Middleware
from fastapi.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

app = FastAPI()

class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str
    lastModifiedAt: datetime

class NewEntry(BaseModel):
    title: str
    content: str = Field(max_length=100000)
    createdBy: str

class UpdateEntry(BaseModel):
    content: str = Field(max_length=100000)
    modifiedBy: str
    summary: str = Field(max_length=500)

class RateLimiterMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.request_count = {}

    async def dispatch(self, request: Request, call_next):
        if request.method in ["POST", "PUT"]:
            client_ip = request.client.host
            if client_ip not in self.request_count:
                self.request_count[client_ip] = 0
            self.request_count[client_ip] += 1
            if self.request_count[client_ip] > 10:  # 10 requests per minute
                return Response(status_code=429, content="Too Many Requests")
        response = await call_next(request)
        return response

app.add_middleware(RateLimiterMiddleware)

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            last_modified_by TEXT NOT NULL,
            last_modified_at TEXT NOT NULL
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
            created_at TEXT NOT NULL,
            FOREIGN KEY (entry_id) REFERENCES entries (id)
        )
    """)
    conn.commit()
    conn.close()

@app.get("/entries")
def list_entries():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM entries")
    rows = cursor.fetchall()
    conn.close()
    html = "<html><body><h1>Entries</h1><ul>"
    for id, title in rows:
        html += f"<li><a href='/entries/{html.escape(id)}'>{html.escape(title)}</a></li>"
    html += "</ul></body></html>"
    return html

@app.post("/entries", status_code=201)
def create_entry(entry: NewEntry):
    entry_id = str(uuid.uuid4())
    current_time = datetime.now().isoformat()
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
        VALUES (?, ?, ?, ?, ?)
    """, (entry_id, entry.title, entry.content, entry.createdBy, current_time))
    cursor.execute("""
        INSERT INTO edits (entry_id, modified_by, summary, old_content, new_content, created_at)
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

@app.get("/entries/{entryId}")
def get_entry(entryId: str = Path(..., regex=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT title, content, last_modified_by, last_modified_at FROM entries WHERE id = ?", (entryId,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Entry not found")
    title, content, last_modified_by, last_modified_at = row
    cursor.execute("SELECT DISTINCT modified_by FROM edits WHERE entry_id = ?", (entryId,))
    contributors = [row[0] for row in cursor.fetchall()]
    conn.close()
    contributors_html = "<ul><li>" + "</li><li>".join(html.escape(c) for c in contributors) + "</li></ul>" if contributors else "<p>No contributors.</p>"
    html = f"""
    <html>
    <body>
    <h1>{html.escape(title)}</h1>
    <p>{html.escape(content)}</p>
    <p>Last modified by {html.escape(last_modified_by)} at {last_modified_at}</p>
    <h2>Contributors:</h2>
    {contributors_html}
    <a href="/entries/{html.escape(entryId)}/edits">View history</a>
    </body>
    </html>
    """
    return html

@app.put("/entries/{entryId}")
def update_entry(entryId: str = Path(..., regex=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"), update_data: UpdateEntry):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT title, content, last_modified_by, last_modified_at FROM entries WHERE id = ?", (entryId,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Entry not found")
    title, old_content, _, _ = row
    current_time = datetime.now().isoformat()
    cursor.execute("""
        UPDATE entries
        SET content = ?, last_modified_by = ?, last_modified_at = ?
        WHERE id = ?
    """, (update_data.content, update_data.modifiedBy, current_time, entryId))
    cursor.execute("""
        INSERT INTO edits (entry_id, modified_by, summary, old_content, new_content, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (entryId, update_data.modifiedBy, update_data.summary, old_content, update_data.content, current_time))
    conn.commit()
    conn.close()
    return Entry(
        id=entryId,
        title=title,
        content=update_data.content,
        lastModifiedBy=update_data.modifiedBy,
        lastModifiedAt=current_time
    )

@app.get("/entries/{entryId}/edits")
def get_edits(entryId: str = Path(..., regex=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM entries WHERE id = ?", (entryId,))
    if cursor.fetchone() is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Entry not found")
    cursor.execute("""
        SELECT modified_by, summary, old_content, new_content, created_at
        FROM edits
        WHERE entry_id = ?
        ORDER BY created_at DESC
    """, (entryId,))
    rows = cursor.fetchall()
    conn.close()
    html = "<html><body><h1>Edit History</h1>"
    if not rows:
        html += "<p>No edits found.</p>"
    else:
        for row in rows:
            modified_by, summary, old_content, new_content, created_at = row
            diff = difflib.ndiff(old_content.splitlines(), new_content.splitlines())
            diff_html = ''.join(f"<div class='diff {line[0]}'>{html.escape(line[2:])}</div>" for line in diff)
            html += f"""
            <div style="border: 1px solid #ccc; padding: 10px; margin-bottom: 10px;">
                <p><strong>Modified by:</strong> {html.escape(modified_by)}</p>
                <p><strong>Summary:</strong> {html.escape(summary)}</p>
                <p><strong>At:</strong> {created_at}</p>
                <div style="white-space: pre-wrap;">{diff_html}</div>
            </div>
            """
    html += "</body></html>"
    return html

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)