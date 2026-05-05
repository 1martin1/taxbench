import sqlite3
import uuid
import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse
import difflib
from jinja2 import Environment, select_autoescape

app = FastAPI()

# Jinja2 environment for HTML templates
env = Environment(autoescape=select_autoescape(['html']))

# Create tables on startup
@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            last_modified_by TEXT NOT NULL,
            last_modified_at TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS edits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL,
            modified_by TEXT NOT NULL,
            modified_at TEXT NOT NULL,
            summary TEXT NOT NULL,
            old_content TEXT NOT NULL,
            new_content TEXT NOT NULL,
            FOREIGN KEY (entry_id) REFERENCES entries(id)
        )
    ''')
    conn.commit()
    conn.close()

# Pydantic models with validation
class Entry(BaseModel):
    id: str
    title: str
    content: str
    lastModifiedBy: str
    lastModifiedAt: str

class NewEntry(BaseModel):
    title: str = Field(..., max_length=255)
    content: str = Field(..., max_length=10000)
    createdBy: str = Field(..., max_length=255)

class UpdateEntry(BaseModel):
    content: str = Field(..., max_length=10000)
    modifiedBy: str = Field(..., max_length=255)
    summary: str = Field(..., max_length=1000)

# HTML templates
LIST_ENTRIES_TEMPLATE = env.from_string('''
<html>
  <body>
    <h1>Entries</h1>
    <ul>
      {% for entry in entries %}
        <li><a href="/entries/{{ entry[0] }}">{{ entry[1] }}</a></li>
      {% endfor %}
    </ul>
  </body>
</html>
''')

ENTRY_TEMPLATE = env.from_string('''
<html>
  <body>
    <h1>{{ entry.title }}</h1>
    <p>{{ entry.content }}</p>
    <p>Last modified by: {{ entry.last_modified_by }} at {{ entry.last_modified_at }}</p>
    <h2>Contributors</h2>
    <ul>
      {% for contrib in contributors %}
        <li>{{ contrib }}</li>
      {% endfor %}
    </ul>
  </body>
</html>
''')

EDITS_TEMPLATE = env.from_string('''
<html>
  <body>
    <h1>Edit History</h1>
    <ul>
      {% for edit in edits %}
        <li>
          <h3>{{ edit.modified_by }} - {{ edit.modified_at }}</h3>
          <p>{{ edit.summary }}</p>
          <pre>{{ edit.diff }}</pre>
        </li>
      {% endfor %}
    </ul>
  </body>
</html>
''')

@app.post("/entries", status_code=201, response_model=Entry)
def create_entry(entry_data: NewEntry):
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        entry_id = str(uuid.uuid4())
        now = datetime.datetime.utcnow().isoformat()
        cursor.execute('''
            INSERT INTO entries (id, title, content, last_modified_by, last_modified_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (entry_id, entry_data.title, entry_data.content, entry_data.createdBy, now))
        conn.commit()
        cursor.execute('SELECT * FROM entries WHERE id = ?', (entry_id,))
        row = cursor.fetchone()
        return Entry(
            id=row[0],
            title=row[1],
            content=row[2],
            lastModifiedBy=row[3],
            lastModifiedAt=row[4]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()

@app.get("/entries", response_class=HTMLResponse)
def list_entries():
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute('SELECT id, title FROM entries')
        entries = cursor.fetchall()
        # Limit entries to prevent large responses
        if len(entries) > 100:
            entries = entries[:100]
        return LIST_ENTRIES_TEMPLATE.render(entries=entries)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()

@app.get("/entries/{entryId}", response_class=HTMLResponse)
def get_entry(entryId: str):
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM entries WHERE id = ?', (entryId,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Entry not found")
        entry = {
            'id': row[0],
            'title': row[1],
            'content': row[2],
            'last_modified_by': row[3],
            'last_modified_at': row[4]
        }
        cursor.execute('SELECT modified_by FROM edits WHERE entry_id = ?', (entryId,))
        edits_modified_bys = [mb for (mb,) in cursor.fetchall()]
        contributors = {entry['last_modified_by']}
        contributors.update(edits_modified_bys)
        contributors = sorted(contributors)
        # Limit contributors to prevent large responses
        if len(contributors) > 100:
            contributors = contributors[:100]
        return ENTRY_TEMPLATE.render(entry=entry, contributors=contributors)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()

@app.put("/entries/{entryId}", response_model=Entry)
def update_entry(entryId: str, update_data: UpdateEntry):
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM entries WHERE id = ?', (entryId,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Entry not found")
        current_content = row[2]
        now = datetime.datetime.utcnow().isoformat()
        cursor.execute('''
            UPDATE entries
            SET content = ?, last_modified_by = ?, last_modified_at = ?
            WHERE id = ?
        ''', (update_data.content, update_data.modifiedBy, now, entryId))
        cursor.execute('''
            INSERT INTO edits (entry_id, modified_by, modified_at, summary, old_content, new_content)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (entryId, update_data.modifiedBy, now, update_data.summary, current_content, update_data.content))
        conn.commit()
        cursor.execute('SELECT * FROM entries WHERE id = ?', (entryId,))
        row = cursor.fetchone()
        return Entry(
            id=row[0],
            title=row[1],
            content=row[2],
            lastModifiedBy=row[3],
            lastModifiedAt=row[4]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()

@app.get("/entries/{entryId}/edits", response_class=HTMLResponse)
def get_edits(entryId: str):
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM entries WHERE id = ?', (entryId,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Entry not found")
        cursor.execute('''
            SELECT modified_by, modified_at, summary, old_content, new_content
            FROM edits
            WHERE entry_id = ?
            ORDER BY modified_at DESC
        ''', (entryId,))
        edits = cursor.fetchall()
        # Limit edits to prevent large responses
        if len(edits) > 100:
            edits = edits[:100]
        rendered_edits = []
        for modified_by, modified_at, summary, old_content, new_content in edits:
            diff = '\n'.join(difflib.unified_diff(
                old_content.splitlines(),
                new_content.splitlines(),
                fromfile='Old',
                tofile='New'
            ))
            rendered_edits.append({
                'modified_by': modified_by,
                'modified_at': modified_at,
                'summary': summary,
                'diff': diff
            })
        return EDITS_TEMPLATE.render(edits=rendered_edits)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)