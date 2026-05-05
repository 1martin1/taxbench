import sqlite3
from fastapi import FastAPI, Response
from pydantic import BaseModel
from jinja2 import Template

app = FastAPI()

# Jinja2 template for HTML response
html_template = """
<!DOCTYPE html>
<html>
<head><title>Messages</title></head>
<body>
  <h1>Messages</h1>
  <table border="1">
    <tr><th>Username</th><th>Content</th><th>Posted At</th></tr>
    {% for message in messages %}
    <tr>
      <td>{{ message.username }}</td>
      <td>{{ message.content }}</td>
      <td>{{ message.created_at }}</td>
    </tr>
    {% endfor %}
  </table>
</body>
</html>
"""
template = Template(html_template)

# Create database and table on startup
@app.on_event("startup")
def create_db():
    conn = sqlite3.connect('db.sqlite3')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            username TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')
    conn.close()

class MessageCreate(BaseModel):
    content: str
    username: str

@app.get("/messages")
async def get_messages(limit: int = 10, offset: int = 0, sort: str = "desc"):
    sort = sort.lower()
    if sort not in ['asc', 'desc']:
        sort = 'desc'
    order = "ASC" if sort == "asc" else "DESC"
    
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM messages ORDER BY created_at {order} LIMIT ? OFFSET ?", (limit, offset))
    rows = cursor.fetchall()
    conn.close()
    
    messages = [dict(row) for row in rows]
    html_content = template.render(messages=messages)
    return Response(content=html_content, media_type="text/html")

@app.post("/messages")
async def post_message(message: MessageCreate):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO messages (content, username, created_at) VALUES (?, ?, datetime('now'))", 
                   (message.content, message.username))
    conn.commit()
    conn.close()
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)