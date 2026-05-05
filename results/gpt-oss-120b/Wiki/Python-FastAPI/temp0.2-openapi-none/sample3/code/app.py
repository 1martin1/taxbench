import uuid
import sqlite3
from datetime import datetime
from typing import List, Optional

import difflib
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(title="Wiki API", description="API for a simple wiki where users can add and edit entries, with change tracking.", version="1.0.0")

DB_PATH = "db.sqlite3"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    # entries table
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
    # edits table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL,
            content TEXT NOT NULL,
            modified_by TEXT NOT NULL,
            summary TEXT,
            timestamp TEXT NOT NULL,
            FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()


@app.on_event("startup")
def startup():
    init_db()


# Pydantic models
class EntryModel(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str = Field(..., alias="last_modified_by")
    lastModifiedAt: datetime = Field(..., alias="last_modified_at")


class NewEntry(BaseModel):
    title: str
    content: str
    createdBy: str = Field(..., alias="created_by")


class UpdateEntry(BaseModel):
    content: str
    modifiedBy: str = Field(..., alias="modified_by")
    summary: Optional[str] = None


# Helper functions
def fetch_entry(entry_id: str) -> Optional[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
    row = cur.fetchone()
    conn.close()
    return row


def fetch_all_entries() -> List[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM entries ORDER BY title")
    rows = cur.fetchall()
    conn.close()
    return rows


def fetch_edits(entry_id: str) -> List[sqlite3.Row]:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM edits WHERE entry_id = ? ORDER BY timestamp ASC", (entry_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def insert_entry(entry_id: str, title: str, content: str, created_by: str):
    now = datetime.utcnow().isoformat()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entry_id, title, content, created_by, now),
    )
    # initial edit record
    cur.execute(
        """
        INSERT INTO edits (entry_id, content, modified_by, summary, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entry_id, content, created_by, "Initial creation", now),
    )
    conn.commit()
    conn.close()


def update_entry(entry_id: str, new_content: str, modified_by: str, summary: Optional[str]):
    now = datetime.utcnow().isoformat()
    conn = get_db()
    cur = conn.cursor()
    # Update entry
    cur.execute(
        """
        UPDATE entries
        SET content = ?, last_modified_by = ?, last_modified_at = ?
        WHERE id = ?
        """,
        (new_content, modified_by, now, entry_id),
    )
    # Insert edit record
    cur.execute(
        """
        INSERT INTO edits (entry_id, content, modified_by, summary, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entry_id, new_content, modified_by, summary, now),
    )
    conn.commit()
    conn.close()


def generate_entry_html(entry: sqlite3.Row) -> str:
    # Get contributors
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT modified_by FROM edits WHERE entry_id = ?", (entry["id"],)
    )
    contributors = [row["modified_by"] for row in cur.fetchall()]
    conn.close()

    html = f"""
    <html>
        <head>
            <title>{entry["title"]}</title>
            <style>
                body {{font-family: Arial, sans-serif; margin: 2rem;}}
                .meta {{color: #555; margin-bottom: 1rem;}}
                .content {{white-space: pre-wrap; border: 1px solid #ddd; padding: 1rem;}}
            </style>
        </head>
        <body>
            <h1>{entry["title"]}</h1>
            <div class="meta">
                <strong>Last modified by:</strong> {entry["last_modified_by"]}<br/>
                <strong>Last modified at:</strong> {entry["last_modified_at"]}<br/>
                <strong>Contributors:</strong> {", ".join(contributors)}
            </div>
            <div class="content">{entry["content"]}</div>
            <hr/>
            <a href="/entries">Back to list</a>
        </body>
    </html>
    """
    return html


def generate_entries_list_html(entries: List[sqlite3.Row]) -> str:
    items = "\n".join(
        f'<li><a href="/entries/{e["id"]}">{e["title"]}</a></li>' for e in entries
    )
    html = f"""
    <html>
        <head>
            <title>Wiki Entries</title>
            <style>
                body {{font-family: Arial, sans-serif; margin: 2rem;}}
                ul {{list-style-type: none; padding: 0;}}
                li {{margin: 0.5rem 0;}}
            </style>
        </head>
        <body>
            <h1>Wiki Entries</h1>
            <ul>
                {items}
            </ul>
            <hr/>
            <h2>Create New Entry</h2>
            <form action="/entries" method="post" enctype="application/json">
                <label>Title: <input type="text" name="title" required/></label><br/><br/>
                <label>Content:<br/>
                <textarea name="content" rows="10" cols="60" required></textarea></label><br/><br/>
                <label>Created By: <input type="text" name="createdBy" required/></label><br/><br/>
                <button type="submit">Create</button>
            </form>
        </body>
    </html>
    """
    return html


def generate_edits_html(entry_id: str, edits: List[sqlite3.Row]) -> str:
    if not edits:
        edits_section = "<p>No edits found.</p>"
    else:
        diffs_html = ""
        previous_content = ""
        for edit in edits:
            current_content = edit["content"]
            diff = difflib.unified_diff(
                previous_content.splitlines(),
                current_content.splitlines(),
                fromfile="previous",
                tofile="current",
                lineterm="",
            )
            diff_text = "\n".join(diff) if diff else "(no changes)"
            diffs_html += f"""
            <div class="edit">
                <p><strong>Edited by:</strong> {edit["modified_by"]} <br/>
                <strong>At:</strong> {edit["timestamp"]} <br/>
                <strong>Summary:</strong> {edit["summary"] or ""}</p>
                <pre class="diff">{diff_text}</pre>
                <hr/>
            </div>
            """
            previous_content = current_content

        edits_section = diffs_html

    html = f"""
    <html>
        <head>
            <title>Edit History</title>
            <style>
                body {{font-family: Arial, sans-serif; margin: 2rem;}}
                .diff {{background:#f8f8f8; padding:1rem; overflow:auto;}}
                .edit {{margin-bottom:2rem;}}
                pre {{margin:0;}}
            </style>
        </head>
        <body>
            <h1>Edit History for Entry {entry_id}</h1>
            {edits_section}
            <hr/>
            <a href="/entries/{entry_id}">Back to entry</a> |
            <a href="/entries">Back to list</a>
        </body>
    </html>
    """
    return html


# Routes
@app.get("/entries", response_class=HTMLResponse)
def list_entries():
    entries = fetch_all_entries()
    return HTMLResponse(content=generate_entries_list_html(entries))


@app.post("/entries", response_model=EntryModel, status_code=status.HTTP_201_CREATED)
def create_entry(new_entry: NewEntry):
    entry_id = str(uuid.uuid4())
    insert_entry(entry_id, new_entry.title, new_entry.content, new_entry.createdBy)
    entry_row = fetch_entry(entry_id)
    if not entry_row:
        raise HTTPException(status_code=500, detail="Failed to retrieve created entry")
    entry = EntryModel(
        id=entry_row["id"],
        title=entry_row["title"],
        content=entry_row["content"],
        last_modified_by=entry_row["last_modified_by"],
        last_modified_at=datetime.fromisoformat(entry_row["last_modified_at"]),
    )
    return entry


@app.get("/entries/{entryId}", response_class=HTMLResponse)
def get_entry(entryId: str):
    entry = fetch_entry(entryId)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    return HTMLResponse(content=generate_entry_html(entry))


@app.put("/entries/{entryId}", response_model=EntryModel)
def update_entry_endpoint(entryId: str, update: UpdateEntry):
    entry = fetch_entry(entryId)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    update_entry(entryId, update.content, update.modifiedBy, update.summary)
    updated = fetch_entry(entryId)
    entry_model = EntryModel(
        id=updated["id"],
        title=updated["title"],
        content=updated["content"],
        last_modified_by=updated["last_modified_by"],
        last_modified_at=datetime.fromisoformat(updated["last_modified_at"]),
    )
    return entry_model


@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
def get_edits(entryId: str):
    entry = fetch_entry(entryId)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    edits = fetch_edits(entryId)
    return HTMLResponse(content=generate_edits_html(entryId, edits))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)