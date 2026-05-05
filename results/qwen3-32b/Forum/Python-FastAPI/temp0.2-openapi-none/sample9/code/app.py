import sqlite3
from fastapi import FastAPI, Response, Query, HTTPException
from pydantic import BaseModel
from datetime import datetime
import os
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

app = FastAPI()

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=400,
        content={"detail": "Invalid input"},
    )

class MessageModel(BaseModel):
    content: str
    username: str

def get_db():
    db_path = 'db.sqlite3'
    conn = sqlite3.connect(db_path, check_same_thread=False)
    return conn

@app.on_event("startup")
def create_table():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            content TEXT NOT NULL,
            submission_time TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def generate_html(messages):
    html = "<html><body><h1>Messages</h1>"
    for msg in messages:
        html += f"<div><strong>{msg['username']}</strong> ({msg['submission_time']}):<br/>{msg['content']}</div><hr/>"
    html += "</body></html>"
    return html

@app.get("/messages")
async def get_messages(
    limit: int = Query(default=10, ge=0),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default="desc", enum=["asc", "desc"])
):
    conn = get_db()
    cursor = conn.cursor()
    order = "DESC" if sort.lower() == "desc" else "ASC"
    query = f"SELECT id, username, content, submission_time FROM messages ORDER BY submission_time {order} LIMIT ? OFFSET ?"
    cursor.execute(query, (limit, offset))
    rows = cursor.fetchall()
    conn.close()

    messages = []
    for row in rows:
        msg = {
            'id': row[0],
            'username': row[1],
            'content': row[2],
            'submission_time': row[3]
        }
        messages.append(msg)

    html_content = generate_html(messages)
    return Response(content=html_content, media_type="text/html")

@app.post("/messages")
async def post_message(message: MessageModel):
    conn = get_db()
    cursor = conn.cursor()
    submission_time = datetime.now().isoformat()
    cursor.execute("INSERT INTO messages (username, content, submission_time) VALUES (?, ?, ?)",
                   (message.username, message.content, submission_time))
    conn.commit()
    conn.close()
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)