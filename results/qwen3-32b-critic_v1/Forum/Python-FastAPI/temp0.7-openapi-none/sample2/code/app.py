from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
import sqlite3
import uvicorn
from jinja2 import Template

app = FastAPI()

@app.on_event("startup")
def create_table():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            content TEXT NOT NULL,
            username TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

class MessageCreate(BaseModel):
    content: str = Field(..., max_length=1000)
    username: str = Field(..., max_length=50)

HTML_TEMPLATE = Template("""
<html>
<body>
<h1>Messages</h1>
<ul>
{% for message in messages %}
<li>Username: <b>{{ message.username|e }}</b> - {{ message.content|e }} (Posted at {{ message.created_at|e }})</li>
{% endfor %}
</ul>
</body>
</html>
""")

@app.get("/messages")
async def get_messages(limit: int = 10, offset: int = 0, sort: str = "desc"):
    if limit < 0 or offset < 0:
        raise HTTPException(status_code=400, detail="Negative values not allowed")
    if limit > 100:
        raise HTTPException(status_code=400, detail="Limit exceeds maximum allowed value of 100")
    
    sort = sort.upper()
    if sort not in ("ASC", "DESC"):
        raise HTTPException(status_code=400, detail="Invalid sort direction")
    
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM messages ORDER BY created_at {sort} LIMIT ? OFFSET ?", (limit, offset))
    rows = cursor.fetchall()
    conn.close()
    
    messages = [
        {
            'username': row[2],
            'content': row[1],
            'created_at': row[3]
        } 
        for row in rows
    ]
    
    html_content = HTML_TEMPLATE.render(messages=messages)
    return Response(content=html_content, media_type="text/html")

@app.post("/messages")
async def post_message(message: MessageCreate):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO messages (content, username) VALUES (?, ?)",
            (message.content, message.username)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Invalid input")
    finally:
        conn.close()
    return {"status": "success"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)