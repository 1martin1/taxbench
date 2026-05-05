import uuid
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional, Dict

import difflib
from fastapi import FastAPI, HTTPException, Request, status, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, constr, validator
from jinja2 import Environment, select_autoescape

# ---------- Configuration ----------
DB_PATH = "db.sqlite3"
MAX_TITLE_LENGTH = 200
MAX_CONTENT_LENGTH = 5000
MAX_SUMMARY_LENGTH = 500
RATE_LIMIT = 100  # requests
RATE_PERIOD = timedelta(minutes=1)

# ---------- Rate limiting ----------
# Simple in‑memory store: {ip: [timestamp, ...]}
_rate_store: Dict[str, List[datetime]] = {}


def rate_limiter(request: Request):
    ip = request.client.host
    now = datetime.utcnow()
    timestamps = _rate_store.get(ip, [])
    # Remove timestamps older than period
    timestamps = [ts for ts in timestamps if now - ts < RATE_PERIOD]
    if len(timestamps) >= RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please try again later.",
        )
    timestamps.append(now)
    _rate_store[ip] = timestamps


# ---------- Pydantic models ----------
class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str = Field(..., alias="lastModifiedBy")
    lastModifiedAt: datetime = Field(..., alias="lastModifiedAt")

    class Config:
        populate_by_name = True
        json_encoders = {datetime: lambda v: v.isoformat()}


class NewEntry(BaseModel):
    title: constr(max_length=MAX_TITLE_LENGTH)
    content: constr(max_length=MAX_CONTENT_LENGTH)
    createdBy: constr(max_length=MAX_TITLE_LENGTH)

    @validator("title", "content", "createdBy")
    def strip_whitespace(cls, v: str) -> str:
        return v.strip()


class UpdateEntry(BaseModel):
    content: constr(max_length=MAX_CONTENT_LENGTH)
    modifiedBy: constr(max_length=MAX_TITLE_LENGTH)
    summary: Optional[constr(max_length=MAX_SUMMARY_LENGTH)] = None

    @validator("content", "modifiedBy", "summary", pre=True, always=True)
    def strip_whitespace(cls, v):
        if isinstance(v, str):
            return v.strip()
        return v


# ---------- FastAPI app ----------
app = FastAPI(
    title="Wiki API",
    description="API for a simple wiki where users can add and edit entries, with change tracking.",
    version="1.0.0",
)


# ---------- Database utilities ----------
def get_connection():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            last_modified_by TEXT NOT NULL,
            last_modified_at TIMESTAMP NOT NULL
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
            modified_at TIMESTAMP NOT NULL,
            FOREIGN KEY(entry_id) REFERENCES entries(id)
        )
        """
    )
    conn.commit()
    conn.close()


@app.on_event("startup")
def on_startup():
    init_db()


# ---------- Jinja2 environment ----------
jinja_env = Environment(autoescape=select_autoescape(["html", "xml"]))

entries_list_template = jinja_env.from_string(
    """
<!DOCTYPE html>
<html>
<head>
    <title>Wiki Entries</title>
</head>
<body>
    <h1>Wiki Entries</h1>
    <ul>
    {% for entry in entries %}
        <li><a href="/entries/{{ entry.id }}">{{ entry.title }}</a></li>
    {% endfor %}
    </ul>
    <h2>Create New Entry</h2>
    <form action="/entries" method="post">
        <label>Title: <input type="text" name="title" maxlength="{{ max_title }}" required></label><br>
        <label>Content:<br><textarea name="content" rows="5" cols="40" maxlength="{{ max_content }}" required></textarea></label><br>
        <label>Created By: <input type="text" name="createdBy" maxlength="{{ max_title }}" required></label><br>
        <button type="submit">Create</button>
    </form>
</body>
</html>
"""
)

entry_detail_template = jinja_env.from_string(
    """
<!DOCTYPE html>
<html>
<head>
    <title>{{ entry.title }}</title>
</head>
<body>
    <h1>{{ entry.title }}</h1>
    <p><em>Last modified by {{ entry.lastModifiedBy }} at {{ entry.lastModifiedAt }}</em></p>
    <div>{{ entry.content }}</div>
    <h3>Contributors</h3>
    <ul>
    {% for contributor in contributors %}
        <li>{{ contributor }}</li>
    {% endfor %}
    </ul>
    <h2>Update Entry</h2>
    <form action="/entries/{{ entry.id }}" method="post">
        <input type="hidden" name="_method" value="PUT">
        <label>Content:<br><textarea name="content" rows="5" cols="40" maxlength="{{ max_content }}" required>{{ entry.content }}</textarea></label><br>
        <label>Modified By: <input type="text" name="modifiedBy" maxlength="{{ max_title }}" required></label><br>
        <label>Summary (optional):<br><textarea name="summary" rows="2" cols="40" maxlength="{{ max_summary }}"></textarea></label><br>
        <button type="submit">Update</button>
    </form>
    <p><a href="/entries/{{ entry.id }}/edits">View edit history</a></p>
    <p><a href="/entries">Back to list</a></p>
</body>
</html>
"""
)

edits_history_template = jinja_env.from_string(
    """
<!DOCTYPE html>
<html>
<head>
    <title>Edit History for {{ entry.title }}</title>
    <style>
        table.diff {font-family:Courier; border:medium;}
        .diff_header {background-color:#e0e0e0}
        td.diff_header {text-align:right}
        .diff_next {background-color:#c0c0c0}
        .diff_add {background-color:#aaffaa}
        .diff_chg {background-color:#ffff77}
        .diff_sub {background-color:#ffaaaa}
    </style>
</head>
<body>
    <h1>Edit History for "{{ entry.title }}"</h1>
    {% for edit in edits %}
        <h3>Edit #{{ loop.index }} - {{ edit.modified_at }} by {{ edit.modified_by }}</h3>
        {% if edit.summary %}
            <p><strong>Summary:</strong> {{ edit.summary }}</p>
        {% endif %}
        {{ edit.diff | safe }}
        <hr>
    {% endfor %}
    <p><a href="/entries/{{ entry.id }}">Back to entry</a></p>
    <p><a href="/entries">Back to list</a></p>
</body>
</html>
"""
)


# ---------- Helper functions ----------
def fetch_entry(entry_id: str) -> Optional[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
    row = cur.fetchone()
    conn.close()
    return row


def fetch_contributors(entry_id: str) -> List[str]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT modified_by FROM edits WHERE entry_id = ? ORDER BY modified_by",
        (entry_id,),
    )
    contributors = [row["modified_by"] for row in cur.fetchall()]
    conn.close()
    return contributors


def insert_edit(
    entry_id: str,
    content: str,
    modified_by: str,
    summary: Optional[str],
    modified_at: datetime,
):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO edits (entry_id, content, modified_by, summary, modified_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entry_id, content, modified_by, summary, modified_at),
    )
    conn.commit()
    conn.close()


def fetch_edits(entry_id: str) -> List[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM edits
        WHERE entry_id = ?
        ORDER BY modified_at ASC
        """,
        (entry_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def generate_diff_html(old: str, new: str) -> str:
    differ = difflib.HtmlDiff(wrapcolumn=80)
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    return differ.make_table(
        old_lines,
        new_lines,
        fromdesc="Previous",
        todesc="Current",
        context=True,
        numlines=3,
    )


# ---------- Endpoints ----------
@app.get("/entries", response_class=HTMLResponse, dependencies=[Depends(rate_limiter)])
def list_entries():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM entries ORDER BY title")
    entries = [{"id": row["id"], "title": row["title"]} for row in cur.fetchall()]
    conn.close()
    html = entries_list_template.render(
        entries=entries,
        max_title=MAX_TITLE_LENGTH,
        max_content=MAX_CONTENT_LENGTH,
    )
    return HTMLResponse(content=html)


@app.post("/entries", status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limiter)])
async def create_entry(request: Request):
    # Accept JSON or form data
    ct = request.headers.get("content-type", "")
    if "application/json" in ct:
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)

    try:
        new_entry = NewEntry(**data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    entry_id = uuid.uuid4().hex
    now = datetime.utcnow()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entry_id, new_entry.title, new_entry.content, new_entry.createdBy, now),
    )
    conn.commit()
    conn.close()

    insert_edit(
        entry_id=entry_id,
        content=new_entry.content,
        modified_by=new_entry.createdBy,
        summary="Initial creation",
        modified_at=now,
    )

    entry = Entry(
        id=entry_id,
        title=new_entry.title,
        content=new_entry.content,
        lastModifiedBy=new_entry.createdBy,
        lastModifiedAt=now,
    )
    # If request came from a browser form, redirect to the entry page
    if "application/json" not in ct:
        return RedirectResponse(url=f"/entries/{entry_id}", status_code=303)
    return JSONResponse(content=entry.model_dump(by_alias=True), status_code=201)


@app.get("/entries/{entry_id}", response_class=HTMLResponse, dependencies=[Depends(rate_limiter)])
def get_entry(entry_id: str):
    row = fetch_entry(entry_id)
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    contributors = fetch_contributors(entry_id)
    entry = {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "lastModifiedBy": row["last_modified_by"],
        "lastModifiedAt": row["last_modified_at"].isoformat(),
    }
    html = entry_detail_template.render(
        entry=entry,
        contributors=contributors,
        max_title=MAX_TITLE_LENGTH,
        max_content=MAX_CONTENT_LENGTH,
        max_summary=MAX_SUMMARY_LENGTH,
    )
    return HTMLResponse(content=html)


@app.put("/entries/{entry_id}", response_model=Entry, dependencies=[Depends(rate_limiter)])
async def update_entry(entry_id: str, request: Request):
    row = fetch_entry(entry_id)
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")

    ct = request.headers.get("content-type", "")
    if "application/json" in ct:
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)

    try:
        update = UpdateEntry(**data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    now = datetime.utcnow()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE entries
        SET content = ?, last_modified_by = ?, last_modified_at = ?
        WHERE id = ?
        """,
        (update.content, update.modifiedBy, now, entry_id),
    )
    conn.commit()
    conn.close()

    insert_edit(
        entry_id=entry_id,
        content=update.content,
        modified_by=update.modifiedBy,
        summary=update.summary,
        modified_at=now,
    )

    updated_entry = Entry(
        id=row["id"],
        title=row["title"],
        content=update.content,
        lastModifiedBy=update.modifiedBy,
        lastModifiedAt=now,
    )
    # For form submissions, redirect back to entry page
    if "application/json" not in ct:
        return RedirectResponse(url=f"/entries/{entry_id}", status_code=303)
    return JSONResponse(content=updated_entry.model_dump(by_alias=True))


@app.get("/entries/{entry_id}/edits", response_class=HTMLResponse, dependencies=[Depends(rate_limiter)])
def get_edits(entry_id: str):
    entry_row = fetch_entry(entry_id)
    if not entry_row:
        raise HTTPException(status_code=404, detail="Entry not found")
    edits = fetch_edits(entry_id)

    diff_entries = []
    previous_content = ""
    for edit in edits:
        current_content = edit["content"]
        diff_html = generate_diff_html(previous_content, current_content)
        diff_entries.append(
            {
                "modified_at": edit["modified_at"].isoformat(),
                "modified_by": edit["modified_by"],
                "summary": edit["summary"],
                "diff": diff_html,
            }
        )
        previous_content = current_content

    entry_info = {"id": entry_row["id"], "title": entry_row["title"]}

    html = edits_history_template.render(entry=entry_info, edits=diff_entries)
    return HTMLResponse(content=html)


# ---------- Run server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)