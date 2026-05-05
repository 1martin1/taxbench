import uuid
import sqlite3
import difflib
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path, Body, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(title="Wiki API", description="API for a simple wiki where users can add and edit entries, with change tracking.", version="1.0.0")

DB_PATH = "db.sqlite3"


# ---------- Pydantic Schemas ----------
class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: Optional[str] = None
    lastModifiedAt: Optional[datetime] = None


class NewEntry(BaseModel):
    title: str
    content: str
    createdBy: str = Field(..., alias="createdBy")


class UpdateEntry(BaseModel):
    content: str
    modifiedBy: str = Field(..., alias="modifiedBy")
    summary: str


# ---------- Database Utilities ----------
def get_db():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    # entries table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            last_modified_by TEXT,
            last_modified_at TIMESTAMP
        )
    """)
    # edits table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL,
            content TEXT NOT NULL,
            modified_by TEXT NOT NULL,
            summary TEXT,
            edited_at TIMESTAMP NOT NULL,
            FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()


@app.on_event("startup")
def startup():
    init_db()


# ---------- HTML Templates ----------
LIST_TEMPLATE = """
<!DOCTYPE html>
<html>
<head><title>Wiki Entries</title></head>
<body>
<h1>Wiki Entries</h1>
<ul>
{% for entry in entries %}
  <li><a href="/entries/{{ entry.id }}">{{ entry.title }}</a></li>
{% endfor %}
</ul>
</body>
</html>
"""

ENTRY_TEMPLATE = """
<!DOCTYPE html>
<html>
<head><title>{{ entry.title }}</title></head>
<body>
<h1>{{ entry.title }}</h1>
<div>{{ entry.content | safe }}</div>
<p><strong>Last modified by:</strong> {{ entry.last_modified_by or "N/A" }}</p>
<p><strong>Last modified at:</strong> {{ entry.last_modified_at or "N/A" }}</p>
<h3>Contributors</h3>
<ul>
{% for contributor in contributors %}
  <li>{{ contributor }}</li>
{% endfor %}
</ul>
<p><a href="/entries/{{ entry.id }}/edits">View edit history</a></p>
</body>
</html>
"""

EDITS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head><title>Edit History for {{ entry.title }}</title></head>
<body>
<h1>Edit History for "{{ entry.title }}"</h1>
{% for edit in edits %}
  <div style="border:1px solid #ccc; padding:10px; margin-bottom:10px;">
    <p><strong>Edited by:</strong> {{ edit.modified_by }}</p>
    <p><strong>At:</strong> {{ edit.edited_at }}</p>
    {% if edit.summary %}
      <p><strong>Summary:</strong> {{ edit.summary }}</p>
    {% endif %}
    <pre style="background:#f8f8f8; padding:10px;">{{ edit.diff }}</pre>
  </div>
{% endfor %}
</body>
</html>
"""

# ---------- Helper Functions ----------
def fetch_entry(entry_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
    row = cur.fetchone()
    conn.close()
    return row


def fetch_contributors(entry_id: str) -> List[str]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT modified_by FROM edits WHERE entry_id = ?", (entry_id,))
    rows = cur.fetchall()
    conn.close()
    return [r["modified_by"] for r in rows if r["modified_by"]]


def fetch_edits(entry_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, content, modified_by, summary, edited_at
        FROM edits
        WHERE entry_id = ?
        ORDER BY edited_at ASC
    """, (entry_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def render_template(template_str: str, **context):
    from jinja2 import Template, select_autoescape

    template = Template(template_str, autoescape=select_autoescape(['html', 'xml']))
    return template.render(**context)


# ---------- API Endpoints ----------
@app.get("/entries", response_class=HTMLResponse, summary="Get a list of all entries with their titles and links to respective entries")
def list_entries():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM entries ORDER BY title ASC")
    entries = cur.fetchall()
    conn.close()
    html = render_template(LIST_TEMPLATE, entries=entries)
    return HTMLResponse(content=html)


@app.post("/entries", response_model=Entry, status_code=status.HTTP_201_CREATED, summary="Create a new entry")
def create_entry(payload: NewEntry = Body(...)):
    entry_id = str(uuid.uuid4())
    now = datetime.utcnow()
    conn = get_db()
    cur = conn.cursor()
    # Insert into entries
    cur.execute("""
        INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
        VALUES (?, ?, ?, ?, ?)
    """, (entry_id, payload.title, payload.content, payload.createdBy, now))
    # Insert initial edit
    cur.execute("""
        INSERT INTO edits (entry_id, content, modified_by, summary, edited_at)
        VALUES (?, ?, ?, ?, ?)
    """, (entry_id, payload.content, payload.createdBy, "Created entry", now))
    conn.commit()
    conn.close()
    entry = Entry(
        id=entry_id,
        title=payload.title,
        content=payload.content,
        lastModifiedBy=payload.createdBy,
        lastModifiedAt=now,
    )
    return JSONResponse(content=entry.model_dump(), status_code=status.HTTP_201_CREATED)


@app.get("/entries/{entryId}", response_class=HTMLResponse, summary="Get a specific entry")
def get_entry(entryId: str = Path(..., description="Entry identifier")):
    row = fetch_entry(entryId)
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    contributors = fetch_contributors(entryId)
    entry_data = {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"].replace("\n", "<br>"),
        "last_modified_by": row["last_modified_by"],
        "last_modified_at": row["last_modified_at"],
    }
    html = render_template(ENTRY_TEMPLATE, entry=entry_data, contributors=contributors)
    return HTMLResponse(content=html)


@app.put("/entries/{entryId}", response_model=Entry, summary="Update an existing entry")
def update_entry(
    entryId: str = Path(..., description="Entry identifier"),
    payload: UpdateEntry = Body(...),
):
    row = fetch_entry(entryId)
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    now = datetime.utcnow()
    conn = get_db()
    cur = conn.cursor()
    # Update entries table
    cur.execute("""
        UPDATE entries
        SET content = ?, last_modified_by = ?, last_modified_at = ?
        WHERE id = ?
    """, (payload.content, payload.modifiedBy, now, entryId))
    # Insert edit record
    cur.execute("""
        INSERT INTO edits (entry_id, content, modified_by, summary, edited_at)
        VALUES (?, ?, ?, ?, ?)
    """, (entryId, payload.content, payload.modifiedBy, payload.summary, now))
    conn.commit()
    conn.close()
    updated_entry = Entry(
        id=row["id"],
        title=row["title"],
        content=payload.content,
        lastModifiedBy=payload.modifiedBy,
        lastModifiedAt=now,
    )
    return JSONResponse(content=updated_entry.model_dump())


@app.get("/entries/{entryId}/edits", response_class=HTMLResponse, summary="View the history of edits for a specific entry")
def view_edits(entryId: str = Path(..., description="Entry identifier")):
    entry_row = fetch_entry(entryId)
    if not entry_row:
        raise HTTPException(status_code=404, detail="Entry not found")
    edits = fetch_edits(entryId)
    # Build diff for each edit compared to previous content
    edit_views = []
    previous_content = ""
    for edit in edits:
        current_content = edit["content"]
        diff_lines = difflib.unified_diff(
            previous_content.splitlines(),
            current_content.splitlines(),
            fromfile="previous",
            tofile="current",
            lineterm="",
        )
        diff_text = "\n".join(diff_lines) if diff_lines else "(no changes)"
        edit_views.append({
            "modified_by": edit["modified_by"],
            "edited_at": edit["edited_at"],
            "summary": edit["summary"],
            "diff": diff_text,
        })
        previous_content = current_content
    html = render_template(
        EDITS_TEMPLATE,
        entry={"title": entry_row["title"]},
        edits=edit_views,
    )
    return HTMLResponse(content=html)


# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)