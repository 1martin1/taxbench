import uuid
import sqlite3
import difflib
import html
from datetime import datetime
from typing import List, Optional

from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    status,
    Form,
    Depends,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, validator

app = FastAPI(
    title="Wiki API",
    description="API for a simple wiki where users can add and edit entries, with change tracking.",
    version="1.0.0",
)

DB_PATH = "db.sqlite3"
MAX_CONTENT_LENGTH = 5000  # characters
MAX_DIFF_LINES = 500  # safeguard for diff generation


def get_db():
    # Allow usage across threads (FastAPI may run handlers in different threads)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
            FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


# Pydantic models with camelCase aliases
class Entry(BaseModel):
    id: str
    title: str
    content: str
    last_modified_by: str = Field(..., alias="lastModifiedBy")
    last_modified_at: datetime = Field(..., alias="lastModifiedAt")

    class Config:
        populate_by_name = True
        json_encoders = {datetime: lambda v: v.isoformat()}


class NewEntry(BaseModel):
    title: str
    content: str
    created_by: str = Field(..., alias="createdBy")

    @validator("content")
    def content_length(cls, v):
        if len(v) > MAX_CONTENT_LENGTH:
            raise ValueError(f"Content exceeds maximum length of {MAX_CONTENT_LENGTH}")
        return v

    class Config:
        populate_by_name = True


class UpdateEntry(BaseModel):
    content: str
    modified_by: str = Field(..., alias="modifiedBy")
    summary: Optional[str] = None

    @validator("content")
    def content_length(cls, v):
        if len(v) > MAX_CONTENT_LENGTH:
            raise ValueError(f"Content exceeds maximum length of {MAX_CONTENT_LENGTH}")
        return v

    class Config:
        populate_by_name = True


# Helper DB functions
def fetch_entry(entry_id: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
    row = cur.fetchone()
    conn.close()
    return row


def fetch_edits(entry_id: str) -> List[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM edits WHERE entry_id = ? ORDER BY modified_at ASC", (entry_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def distinct_contributors(entry_id: str) -> List[str]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT modified_by FROM edits WHERE entry_id = ?", (entry_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return [r["modified_by"] for r in rows]


# Routes
@app.get(
    "/entries",
    response_class=HTMLResponse,
    summary="Get a list of all entries with their titles and links to respective entries",
)
def list_entries():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM entries ORDER BY title ASC")
    entries = cur.fetchall()
    conn.close()

    html_items = "\n".join(
        f'<li><a href="/entries/{e["id"]}">{html.escape(e["title"])}</a></li>'
        for e in entries
    )
    html_content = f"""
    <html>
        <head><title>Wiki Entries</title></head>
        <body>
            <h1>All Wiki Entries</h1>
            <ul>
                {html_items}
            </ul>
            <h2>Create a new entry</h2>
            <form id="create-form">
                <label>Title: <input type="text" name="title" required></label><br>
                <label>Content:<br><textarea name="content" rows="10" cols="50" required></textarea></label><br>
                <label>Created By: <input type="text" name="created_by" required></label><br>
                <button type="submit">Create</button>
            </form>
            <script>
                document.getElementById('create-form').addEventListener('submit', async (e) => {{
                    e.preventDefault();
                    const form = e.target;
                    const data = {{
                        title: form.title.value,
                        content: form.content.value,
                        createdBy: form.created_by.value
                    }};
                    const resp = await fetch('/entries', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify(data)
                    }});
                    if (resp.ok) {{
                        const entry = await resp.json();
                        window.location.href = `/entries/${{entry.id}}`;
                    }} else {{
                        const err = await resp.json();
                        alert('Error: ' + JSON.stringify(err));
                    }}
                }});
            </script>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.post(
    "/entries",
    response_model=Entry,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new entry",
)
def create_entry(new_entry: NewEntry):
    entry_id = str(uuid.uuid4())
    now_iso = datetime.utcnow().isoformat()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            entry_id,
            new_entry.title,
            new_entry.content,
            new_entry.created_by,
            now_iso,
        ),
    )
    cur.execute(
        """
        INSERT INTO edits (entry_id, content, modified_by, summary, modified_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            entry_id,
            new_entry.content,
            new_entry.created_by,
            "Created entry",
            now_iso,
        ),
    )
    conn.commit()
    conn.close()
    entry = Entry(
        id=entry_id,
        title=new_entry.title,
        content=new_entry.content,
        last_modified_by=new_entry.created_by,
        last_modified_at=datetime.fromisoformat(now_iso),
    )
    return JSONResponse(content=entry.model_dump(by_alias=True))


@app.get(
    "/entries/{entryId}",
    response_class=HTMLResponse,
    summary="Get a specific entry",
    responses={404: {"description": "Entry not found"}},
)
def get_entry(entryId: str):
    row = fetch_entry(entryId)
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    contributors = distinct_contributors(entryId)
    contributors_html = ", ".join(map(html.escape, contributors)) if contributors else "None"
    escaped_title = html.escape(row["title"])
    escaped_content = html.escape(row["content"]).replace("\n", "<br>")
    html_content = f"""
    <html>
        <head><title>{escaped_title}</title></head>
        <body>
            <h1>{escaped_title}</h1>
            <p><em>Last modified by {html.escape(row["last_modified_by"])} at {html.escape(row["last_modified_at"])}</em></p>
            <div>{escaped_content}</div>
            <p><strong>Contributors:</strong> {contributors_html}</p>
            <hr>
            <h2>Edit this entry</h2>
            <form method="post" action="/entries/{entryId}?_method=PUT">
                <label>Content:<br><textarea name="content" rows="10" cols="50" required>{html.escape(row["content"])}</textarea></label><br>
                <label>Modified By: <input type="text" name="modified_by" required></label><br>
                <label>Summary: <input type="text" name="summary"></label><br>
                <button type="submit">Update</button>
            </form>
            <p><a href="/entries/{entryId}/edits">View edit history</a></p>
            <p><a href="/entries">Back to list</a></p>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.put(
    "/entries/{entryId}",
    response_model=Entry,
    summary="Update an existing entry",
    responses={404: {"description": "Entry not found"}},
)
def update_entry(entryId: str, update: UpdateEntry):
    entry_row = fetch_entry(entryId)
    if not entry_row:
        raise HTTPException(status_code=404, detail="Entry not found")
    now_iso = datetime.utcnow().isoformat()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE entries
        SET content = ?, last_modified_by = ?, last_modified_at = ?
        WHERE id = ?
        """,
        (update.content, update.modified_by, now_iso, entryId),
    )
    cur.execute(
        """
        INSERT INTO edits (entry_id, content, modified_by, summary, modified_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entryId, update.content, update.modified_by, update.summary, now_iso),
    )
    conn.commit()
    conn.close()
    updated_entry = Entry(
        id=entryId,
        title=entry_row["title"],
        content=update.content,
        last_modified_by=update.modified_by,
        last_modified_at=datetime.fromisoformat(now_iso),
    )
    return JSONResponse(content=updated_entry.model_dump(by_alias=True))


@app.get(
    "/entries/{entryId}/edits",
    response_class=HTMLResponse,
    summary="View the history of edits for a specific entry",
    responses={404: {"description": "Entry not found"}},
)
def view_edits(entryId: str):
    entry = fetch_entry(entryId)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    edits = fetch_edits(entryId)

    if not edits:
        # Return an empty history page with 200 status as per spec
        html_content = f"""
        <html>
            <head><title>Edit History for {html.escape(entry["title"])}</title></head>
            <body>
                <h1>Edit History for "{html.escape(entry["title"])}"</h1>
                <p>No edits have been made yet.</p>
                <p><a href="/entries/{entryId}">Back to entry</a></p>
                <p><a href="/entries">Back to list</a></p>
            </body>
        </html>
        """
        return HTMLResponse(content=html_content)

    edit_blocks = []
    previous_content = ""
    for edit in edits:
        current_content = edit["content"]
        # Guard against huge diffs
        if (
            len(previous_content) > MAX_CONTENT_LENGTH
            or len(current_content) > MAX_CONTENT_LENGTH
        ):
            diff_html = "<em>Diff omitted due to size constraints.</em>"
        else:
            diff_lines = list(
                difflib.unified_diff(
                    previous_content.splitlines(),
                    current_content.splitlines(),
                    fromfile="previous",
                    tofile="current",
                    lineterm="",
                )
            )
            if len(diff_lines) > MAX_DIFF_LINES:
                diff_html = "<em>Diff too large to display.</em>"
            else:
                diff_html = "<br>".join(
                    html.escape(line).replace(" ", "&nbsp;") for line in diff_lines
                ) or "(no changes)"
        block = f"""
        <div style="border:1px solid #ccc; padding:10px; margin-bottom:10px;">
            <p><strong>Edited by:</strong> {html.escape(edit["modified_by"])} at {html.escape(edit["modified_at"])}</p>
            <p><strong>Summary:</strong> {html.escape(edit["summary"] or "N/A")}</p>
            <pre style="background:#f8f8f8; padding:5px;">{diff_html}</pre>
        </div>
        """
        edit_blocks.append(block)
        previous_content = current_content

    edits_html = "\n".join(edit_blocks)
    html_content = f"""
    <html>
        <head><title>Edit History for {html.escape(entry["title"])}</title></head>
        <body>
            <h1>Edit History for "{html.escape(entry["title"])}"</h1>
            {edits_html}
            <p><a href="/entries/{entryId}">Back to entry</a></p>
            <p><a href="/entries">Back to list</a></p>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# Support HTML form submissions that emulate PUT via query param
@app.post(
    "/entries/{entryId}",
    include_in_schema=False,
)
def html_form_update(entryId: str, request: Request):
    """
    Handles HTML form submissions that use POST with a query param `_method=PUT`.
    Extracts form data and forwards to the update logic.
    """
    if request.query_params.get("_method") != "PUT":
        raise HTTPException(status_code=405, detail="Method Not Allowed")
    form = request.form()
    # Since request.form() is async, we need to run it accordingly
    async def _process():
        form_data = await form
        content = form_data.get("content")
        modified_by = form_data.get("modified_by")
        summary = form_data.get("summary")
        if not content or not modified_by:
            raise HTTPException(status_code=400, detail="Missing required fields")
        update = UpdateEntry(content=content, modified_by=modified_by, summary=summary)
        response = update_entry(entryId, update)
        # After successful update, redirect back to the entry page
        return RedirectResponse(url=f"/entries/{entryId}", status_code=303)

    return _process()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)