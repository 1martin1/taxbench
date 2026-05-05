from fastapi import FastAPI, Response
from pydantic import BaseModel
from enum import Enum
import sqlite3

class SortOrder(str, Enum):
    ASC = "ASC"
    DESC = "DESC"

app = FastAPI()

class MessageCreate(BaseModel):
    content: str
    username: str

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

@app.post("/messages")
async def post_message(message: MessageCreate):
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO messages (username, content) VALUES (?, ?)", 
                   (message.username, message.content))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/messages")
async def get_messages(limit: int = 10, offset: int = 0, sort: SortOrder = SortOrder.DESC):
    conn = sqlite3.connect("db.sqlite3")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    order = sort.value
    query = f"SELECT * FROM messages ORDER BY created_at {order} LIMIT ? OFFSET ?"
    cursor.execute(query, (limit, offset))
    messages = cursor.fetchall()
    conn.close()

    html_content = "<html><body><h1>Messages</h1><ul>"
    for msg in messages:
        html_content += f"<li><strong>{msg['username']}</strong>: {msg['content']} <em>({msg['created_at']})</em></li>"
    html_content += "</ul></body></html>"

    return Response(content=html_content, media_type="text/html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)