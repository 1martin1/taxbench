from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
import sqlite3

app = FastAPI()

class MessageCreate(BaseModel):
    content: str = Field(..., max_length=1000)
    username: str = Field(..., max_length=100)

@app.on_event("startup")
def create_table():
    with sqlite3.connect('db.sqlite3') as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS messages
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      username TEXT NOT NULL,
                      content TEXT NOT NULL,
                      created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

@app.get("/messages")
def get_messages(limit: int = 10, offset: int = 0, sort: str = 'desc'):
    if limit <= 0:
        raise HTTPException(status_code=400, detail="Limit must be a positive integer")
    if limit > 100:
        raise HTTPException(status_code=400, detail="Limit exceeds maximum allowed value of 100")
    if offset < 0:
        raise HTTPException(status_code=400, detail="Offset cannot be negative")
    
    sort_lower = sort.lower()
    valid_sorts = {'asc', 'desc'}
    if sort_lower not in valid_sorts:
        raise HTTPException(status_code=400, detail="Invalid sort value")
    sort_order = 'DESC' if sort_lower == 'desc' else 'ASC'
    
    with sqlite3.connect('db.sqlite3') as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        query = f"SELECT id, username, content, created_at FROM messages ORDER BY created_at {sort_order} LIMIT ? OFFSET ?"
        cursor.execute(query, (limit, offset))
        rows = cursor.fetchall()
    
    html = "<html><body><h1>Messages</h1><ul>"
    for row in rows:
        html += f"<li><strong>{row['username']}</strong> ({row['created_at']}): {row['content']}</li>"
    html += "</ul></body></html>"
    return Response(content=html, media_type="text/html")

@app.post("/messages")
def post_message(message: MessageCreate):
    try:
        with sqlite3.connect('db.sqlite3') as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO messages (username, content) VALUES (?, ?)", 
                          (message.username, message.content))
            conn.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Invalid input")
    return Response(status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)