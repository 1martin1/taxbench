import sqlite3
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
import difflib
import html
import uvicorn


DB_PATH = "db.sqlite3"

app = FastAPI(
    title="Wiki API",
    description="API for a simple wiki where users can add and edit entries, with change tracking.",
    version="1.0.0",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Ensure foreign key constraints are enforced
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    conn = get_db_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_modified_by TEXT NOT NULL,
                last_modified_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS edits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT NOT NULL,
                previous_content TEXT,
                new_content TEXT NOT NULL,
                modified_by TEXT NOT NULL,
                summary TEXT NOT NULL,
                modified_at TEXT NOT NULL,
                FOREIGN KEY(entry_id) REFERENCES entries(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_edits_entry_id ON edits(entry_id);
            """
        )
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def on_startup():
    init_db()


# Pydantic models (match JSON property names)
class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str
    lastModifiedAt: str


class NewEntry(BaseModel):
    title: str
    content: str
    createdBy: str


class UpdateEntry(BaseModel):
    content: str
    modifiedBy: str
    summary: str


def row_to_entry_model(row: sqlite3.Row) -> Entry:
    return Entry(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        lastModifiedBy=row["last_modified_by"],
        lastModifiedAt=row["last_modified_at"],
    )


def get_entry_row(conn: sqlite3.Connection, entry_id: str) -> Optional[sqlite3.Row]:
    cur = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
    return cur.fetchone()


@app.get("/entries", response_class=HTMLResponse, summary="Get a list of all entries with their titles and links to respective entries")
def list_entries():
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT id, title, last_modified_at FROM entries ORDER BY last_modified_at DESC"
        ).fetchall()
    finally:
        conn.close()

    items_html = []
    for r in rows:
        eid = html.escape(r["id"])
        title = html.escape(r["title"])
        lma = html.escape(r["last_modified_at"])
        items_html.append(f'<li><a href="/entries/{eid}">{title}</a> <small>(last edited {lma})</small></li>')

    body = f"""
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8" />
        <title>Wiki Entries</title>
        <style>
          body {{ font-family: Arial, sans-serif; margin: 2rem; }}
          .container {{ max-width: 900px; margin: 0 auto; }}
          h1 {{ margin-bottom: 0.5rem; }}
          ul {{ line-height: 1.8; }}
          code {{ background: #f5f5f5; padding: 0.2rem 0.4rem; border-radius: 4px; }}
          .hint {{ margin-top: 1rem; color: #555; font-size: 0.95rem; }}
          .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }}
        </style>
      </head>
      <body>
        <div class="container">
          <h1>Wiki Entries</h1>
          <ul>
            {''.join(items_html) if items_html else '<li>No entries yet.</li>'}
          </ul>
          <div class="hint">
            <p>Create a new entry with a JSON POST request to <code>/entries</code>:</p>
            <pre class="mono">curl -s -X POST http://localhost:5000/entries -H "Content-Type: application/json" -d '{{
  "title": "My Page",
  "content": "Hello world",
  "createdBy": "alice"
}}'</pre>
          </div>
        </div>
      </body>
    </html>
    """
    return HTMLResponse(content=body)


@app.post("/entries", status_code=status.HTTP_201_CREATED, response_model=Entry, summary="Create a new entry")
def create_entry(payload: NewEntry):
    eid = uuid.uuid4().hex
    now = utc_now_iso()

    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO entries (id, title, content, created_by, created_at, last_modified_by, last_modified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (eid, payload.title, payload.content, payload.createdBy, now, payload.createdBy, now),
            )
            # Record initial creation as an edit with empty previous content
            conn.execute(
                """
                INSERT INTO edits (entry_id, previous_content, new_content, modified_by, summary, modified_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (eid, "", payload.content, payload.createdBy, "Created entry", now),
            )
        row = get_entry_row(conn, eid)
        assert row is not None
        return row_to_entry_model(row)
    finally:
        conn.close()


@app.get(
    "/entries/{entryId}",
    response_class=HTMLResponse,
    summary="Get a specific entry",
    responses={404: {"description": "Entry not found"}},
)
def get_entry(entryId: str = Path(..., description="Entry ID")):
    conn = get_db_connection()
    try:
        row = get_entry_row(conn, entryId)
        if not row:
            raise HTTPException(status_code=404, detail="Entry not found")

        # Contributors: created_by + distinct editors from edits
        contribs = set()
        contribs.add(row["created_by"])
        c = conn.execute(
            "SELECT DISTINCT modified_by FROM edits WHERE entry_id = ? ORDER BY modified_by",
            (entryId,),
        )
        contribs.update([r["modified_by"] for r in c.fetchall()])
        contrib_list = sorted(contribs, key=lambda x: (x.lower(), x))

        title = html.escape(row["title"])
        content = html.escape(row["content"])
        last_mod_by = html.escape(row["last_modified_by"])
        last_mod_at = html.escape(row["last_modified_at"])
        contribs_html = ", ".join(html.escape(x) for x in contrib_list)

        body = f"""
        <!DOCTYPE html>
        <html>
          <head>
            <meta charset="utf-8" />
            <title>{title} - Wiki</title>
            <style>
              body {{ font-family: Arial, sans-serif; margin: 2rem; }}
              .container {{ max-width: 900px; margin: 0 auto; }}
              pre {{ background: #f8f8f8; padding: 1rem; border-radius: 6px; overflow-x: auto; }}
              .meta {{ color: #555; margin-bottom: 1rem; }}
              a.button {{ display: inline-block; padding: 0.4rem 0.7rem; border: 1px solid #ccc; border-radius: 4px; text-decoration: none; color: #333; background: #f6f6f6; }}
              .row {{ margin: 0.3rem 0; }}
              .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }}
            </style>
          </head>
          <body>
            <div class="container">
              <div class="row"><a class="button" href="/entries">← Back to entries</a></div>
              <h1>{title}</h1>
              <div class="meta">Last edited by <strong>{last_mod_by}</strong> at <span class="mono">{last_mod_at}</span></div>
              <div class="meta">Contributors: {contribs_html if contribs_html else "None"}</div>
              <h2>Content</h2>
              <pre>{content}</pre>
              <div class="row">
                <a class="button" href="/entries/{html.escape(entryId)}/edits">View edit history</a>
              </div>
            </div>
          </body>
        </html>
        """
        return HTMLResponse(content=body)
    finally:
        conn.close()


@app.put(
    "/entries/{entryId}",
    response_model=Entry,
    responses={404: {"description": "Entry not found"}},
    summary="Update an existing entry",
)
def update_entry(entryId: str, payload: UpdateEntry):
    conn = get_db_connection()
    try:
        row = get_entry_row(conn, entryId)
        if not row:
            raise HTTPException(status_code=404, detail="Entry not found")

        previous_content = row["content"]
        now = utc_now_iso()

        with conn:
            # Record edit
            conn.execute(
                """
                INSERT INTO edits (entry_id, previous_content, new_content, modified_by, summary, modified_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (entryId, previous_content, payload.content, payload.modifiedBy, payload.summary, now),
            )
            # Update entry
            conn.execute(
                """
                UPDATE entries
                SET content = ?, last_modified_by = ?, last_modified_at = ?
                WHERE id = ?
                """,
                (payload.content, payload.modifiedBy, now, entryId),
            )
        updated = get_entry_row(conn, entryId)
        assert updated is not None
        return row_to_entry_model(updated)
    finally:
        conn.close()


@app.get(
    "/entries/{entryId}/edits",
    response_class=HTMLResponse,
    responses={404: {"description": "Entry not found"}},
    summary="View the history of edits for a specific entry",
)
def list_edits(entryId: str):
    conn = get_db_connection()
    try:
        entry_row = get_entry_row(conn, entryId)
        if not entry_row:
            raise HTTPException(status_code=404, detail="Entry not found")

        edits = conn.execute(
            """
            SELECT id, previous_content, new_content, modified_by, summary, modified_at
            FROM edits
            WHERE entry_id = ?
            ORDER BY modified_at ASC, id ASC
            """,
            (entryId,),
        ).fetchall()

        title = html.escape(entry_row["title"])

        htmldiff = difflib.HtmlDiff(wrapcolumn=80)
        sections: List[str] = []
        for e in edits:
            contributor = html.escape(e["modified_by"])
            summary = html.escape(e["summary"])
            when = html.escape(e["modified_at"])
            prev_lines = (e["previous_content"] or "").splitlines()
            new_lines = (e["new_content"] or "").splitlines()
            diff_table = htmldiff.make_table(
                prev_lines,
                new_lines,
                fromdesc="Before",
                todesc="After",
                context=True,
                numlines=3,
            )
            sections.append(
                f"""
                <section class="edit">
                  <div class="header">
                    <strong>{contributor}</strong> at <span class="mono">{when}</span>
                    <div class="summary">Summary: {summary}</div>
                  </div>
                  <div class="diff">{diff_table}</div>
                </section>
                """
            )

        body = f"""
        <!DOCTYPE html>
        <html>
          <head>
            <meta charset="utf-8" />
            <title>Edit history - {title}</title>
            <style>
              body {{ font-family: Arial, sans-serif; margin: 2rem; }}
              .container {{ max-width: 1100px; margin: 0 auto; }}
              .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }}
              a.button {{ display: inline-block; padding: 0.4rem 0.7rem; border: 1px solid #ccc; border-radius: 4px; text-decoration: none; color: #333; background: #f6f6f6; }}
              .edit {{ margin-bottom: 2rem; }}
              .summary {{ color: #555; margin-top: 0.2rem; }}
              table.diff {{ font-family: monospace; border: 1px solid #ccc; border-collapse: collapse; width: 100%; }}
              .diff th, .diff td {{ border: 1px solid #ddd; padding: 0.2rem 0.3rem; vertical-align: top; }}
              .diff thead th {{ background: #f0f0f0; }}
            </style>
          </head>
          <body>
            <div class="container">
              <div><a class="button" href="/entries/{html.escape(entryId)}">← Back to entry</a></div>
              <h1>Edit history: {title}</h1>
              {'\n'.join(sections) if sections else '<p>No edits recorded.</p>'}
            </div>
          </body>
        </html>
        """
        return HTMLResponse(content=body)
    finally:
        conn.close()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)