from fastapi import FastAPI, Response
import sqlite3
from pydantic import BaseModel
from enum import Enum

app = FastAPI()

class Message(BaseModel):
    username: str
    content: str

def init_db():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/messages")
async def get_messages(limit: int = 10, offset: int = 0, sort: str = "desc"):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    sort_order = "DESC" if sort.lower() == "desc" else "ASC"
    query = f"SELECT username, content, created_at FROM messages ORDER BY created_at {sort_order} LIMIT ? OFFSET ?"
    cursor.execute(query, (limit, offset))
    rows = cursor.fetchall()
    conn.close()

    html_content = "<html><body><h1>Messages</h1><ul>"
    for row in rows:
        username, content, created_at = row
        html_content += f"<li><strong>{username}</strong> ({created_at}): {content}</li>"
    html_content += "</ul></body></html>"

    return Response(content=html_content, media_type="text/html")

@app.post("/messages")
async def post_message(message: Message):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO messages (username, content) VALUES (?, ?)",
                   (message.username, message.content))
    conn.commit()
    conn.close()
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)