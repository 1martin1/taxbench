import uuid
import sqlite3
import difflib
import html
from datetime import datetime
from typing import List, Optional, Dict

from fastapi import FastAPI, HTTPException, Request, status, Depends
from fastapi.responses import HTMLResponse
import uvicorn
from pydantic import BaseModel, Field

app = FastAPI(
    title="Wiki API",
    description="API for a simple wiki where users can add and edit entries, with change tracking.",
    version="1.0.0",
)

DB_PATH = "db.sqlite3"
MAX_CONTENT_LENGTH = 1_000_000          # 1 MB limit for request bodies
MAX_DIFF_SIZE = 500_000                 # 500 KB limit for diff generation


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            last_modified_by TEXT NOT NULL,
            last_modified_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL,
            content TEXT NOT NULL,
            modified_by TEXT NOT NULL,
            summary TEXT,
            modified_at TEXT NOT NULL,
            FOREIGN KEY(entry_id) REFERENCES entries(id)
        )
        """
    )
    conn.commit()
    conn.close()


@app.on_event("startup")
def startup():
    init_db()


# ---------- Schemas ----------
class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str = Field(..., alias="last_modified_by")
    lastModifiedAt: str = Field(..., alias="last_modified_at")

    class Config:
        allow_population_by_field_name = True
        orm_mode = True


class NewEntry(BaseModel):
    title: str
    content: str
    createdBy: str

    class Config:
        allow_population_by_field_name = True


class UpdateEntry(BaseModel):
    content: str
    modifiedBy: str
    summary: str  # required as per OpenAPI spec

    class Config:
        allow_population_by_field_name = True


# ---------- Helper Functions ----------
def fetch_entry(entry_id: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
    entry = cur.fetchone()
    conn.close()
    return entry


def fetch_edits(entry_id: str) -> List[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM edits WHERE entry_id = ? ORDER BY modified_at ASC", (entry_id,)
    )
    edits = cur.fetchall()
    conn.close()
    return edits


def compute_diff(old: str, new: str) -> str:
    """Generate an HTML‑escaped unified diff, with size guard."""
    if len(old) > MAX_DIFF_SIZE or len(new) > MAX_DIFF_SIZE:
        return html.escape("[Diff too large to display]")
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    diff = difflib.unified_diff(old_lines, new_lines, lineterm="")
    escaped = [html.escape(line) for line in diff]
    return "<br>".join(escaped)


def _enforce_content_length(request: Request):
    """Raise 413 if Content-Length exceeds limit."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_CONTENT_LENGTH:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Payload too large",
                )
        except ValueError:
            pass  # ignore malformed header; size will be checked after reading


def _apply_update(entry_id: str, data: Dict) -> Entry:
    """Validate, update DB and return the updated Entry model."""
    # Validate payload using UpdateEntry schema
    try:
        update = UpdateEntry(**data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if len(update.content) > MAX_CONTENT_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Content too large",
        )

    now = datetime.utcnow().isoformat()
    conn = get_db()
    cur = conn.cursor()
    # Update entries table
    cur.execute(
        """
        UPDATE entries
        SET content = ?, last_modified_by = ?, last_modified_at = ?
        WHERE id = ?
        """,
        (update.content, update.modifiedBy, now, entry_id),
    )
    # Insert edit record
    cur.execute(
        """
        INSERT INTO edits (entry_id, content, modified_by, summary, modified_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entry_id, update.content, update.modifiedBy, update.summary, now),
    )
    conn.commit()
    # Fetch title (unchanged) for response
    cur.execute("SELECT title FROM entries WHERE id = ?", (entry_id,))
    title_row = cur.fetchone()
    conn.close()

    return Entry(
        id=entry_id,
        title=title_row["title"] if title_row else "",
        content=update.content,
        last_modified_by=update.modifiedBy,
        last_modified_at=now,
    )


# ---------- Routes ----------
@app.get(
    "/entries",
    response_class=HTMLResponse,
    summary="Get a list of all entries with their titles and links to respective entries",
)
def list_entries():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM entries ORDER BY title ASC")
    rows = cur.fetchall()
    conn.close()

    html_parts = ["<h1>Wiki Entries</h1>", "<ul>"]
    for row in rows:
        entry_id = html.escape(row["id"])
        title = html.escape(row["title"])
        html_parts.append(f'<li><a href="/entries/{entry_id}">{title}</a></li>')
    html_parts.append("</ul>")
    html_parts.append(
        """
        <h2>Create New Entry</h2>
        <form action="/entries" method="post">
            <label>Title: <input type="text" name="title" required></label><br>
            <label>Content:<br><textarea name="content" rows="10" cols="60" required></textarea></label><br>
            <label>Created By: <input type="text" name="createdBy" required></label><br>
            <button type="submit">Create</button>
        </form>
        """
    )
    return HTMLResponse("\n".join(html_parts))


@app.post(
    "/entries",
    response_model=Entry,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new entry",
)
async def create_entry(request: Request):
    _enforce_content_length(request)

    if request.headers.get("content-type", "").startswith("application/json"):
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)

    try:
        new_entry = NewEntry(**data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if len(new_entry.content) > MAX_CONTENT_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Content too large",
        )

    entry_id = uuid.uuid4().hex
    now = datetime.utcnow().isoformat()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entry_id, new_entry.title, new_entry.content, new_entry.createdBy, now),
    )
    # Initial edit record
    cur.execute(
        """
        INSERT INTO edits (entry_id, content, modified_by, summary, modified_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entry_id, new_entry.content, new_entry.createdBy, "Initial creation", now),
    )
    conn.commit()
    conn.close()
    return Entry(
        id=entry_id,
        title=new_entry.title,
        content=new_entry.content,
        last_modified_by=new_entry.createdBy,
        last_modified_at=now,
    )


@app.get(
    "/entries/{entryId}",
    response_class=HTMLResponse,
    summary="Get a specific entry",
)
def get_entry(entryId: str):
    entry = fetch_entry(entryId)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT modified_by FROM edits WHERE entry_id = ?", (entryId,)
    )
    contributors_rows = cur.fetchall()
    contributors = [html.escape(row["modified_by"]) for row in contributors_rows]
    conn.close()

    html_parts = [
        f"<h1>{html.escape(entry['title'])}</h1>",
        f"<p><em>Last modified by {html.escape(entry['last_modified_by'])} at {html.escape(entry['last_modified_at'])}</em></p>",
        f"<div>{html.escape(entry['content']).replace('\n', '<br>')}</div>",
        "<h3>Contributors</h3>",
        "<ul>",
    ]
    for contrib in contributors:
        html_parts.append(f"<li>{contrib}</li>")
    html_parts.append("</ul>")
    html_parts.append(
        f'''
        <h2>Edit Entry</h2>
        <form action="/entries/{html.escape(entryId)}" method="post">
            <input type="hidden" name="_method" value="PUT">
            <label>Content:<br><textarea name="content" rows="10" cols="60" required>{html.escape(entry['content'])}</textarea></label><br>
            <label>Modified By: <input type="text" name="modifiedBy" required></label><br>
            <label>Summary: <input type="text" name="summary" required></label><br>
            <button type="submit">Update</button>
        </form>
        <p><a href="/entries/{html.escape(entryId)}/edits">View edit history</a></p>
        '''
    )
    return HTMLResponse("\n".join(html_parts))


@app.put(
    "/entries/{entryId}",
    response_model=Entry,
    summary="Update an existing entry",
)
async def update_entry(entryId: str, request: Request):
    _enforce_content_length(request)

    entry = fetch_entry(entryId)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    if request.headers.get("content-type", "").startswith("application/json"):
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)

    # Remove any potential method override field
    data.pop("_method", None)

    return _apply_update(entryId, data)


@app.post(
    "/entries/{entryId}",
    response_model=Entry,
    summary="Handle HTML form submission for entry update (method override)",
)
async def update_entry_form(entryId: str, request: Request):
    """
    HTML forms cannot send PUT requests directly.
    This endpoint accepts a POST with a hidden `_method=PUT` field and forwards to the update logic.
    """
    _enforce_content_length(request)

    entry = fetch_entry(entryId)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    if request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(status_code=400, detail="JSON not supported for form submission")
    form = await request.form()
    data = dict(form)

    method = data.pop("_method", "").upper()
    if method != "PUT":
        raise HTTPException(status_code=400, detail="Invalid method override")

    return _apply_update(entryId, data)


@app.get(
    "/entries/{entryId}/edits",
    response_class=HTMLResponse,
    summary="View the history of edits for a specific entry",
)
def view_edits(entryId: str):
    entry = fetch_entry(entryId)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    edits = fetch_edits(entryId)

    html_parts = [
        f"<h1>Edit History for {html.escape(entry['title'])}</h1>",
        "<ul>",
    ]
    previous_content = ""
    if not edits:
        html_parts.append("<li>No edits found for this entry.</li>")
    else:
        for edit in edits:
            modified_at = html.escape(edit["modified_at"])
            modified_by = html.escape(edit["modified_by"])
            summary = html.escape(edit["summary"] or "")
            content = edit["content"]
            diff_html = compute_diff(previous_content, content)
            html_parts.append("<li>")
            html_parts.append(f"<strong>{modified_by}</strong> at {modified_at}<br>")
            if summary:
                html_parts.append(f"<em>Summary: {summary}</em><br>")
            html_parts.append("<pre style='background:#f0f0f0;padding:10px;'>")
            html_parts.append(diff_html)
            html_parts.append("</pre>")
            html_parts.append("</li>")
            previous_content = content
    html_parts.append("</ul>")
    html_parts.append(f'<p><a href="/entries/{html.escape(entryId)}">Back to entry</a></p>')

    return HTMLResponse("\n".join(html_parts))


# ---------- Run Server ----------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)