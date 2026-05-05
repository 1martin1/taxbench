import sqlite3
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException

app = FastAPI()

def init_db():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clicks (
            id TEXT PRIMARY KEY,
            timestamp DATETIME NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

@app.post("/click", status_code=201)
async def register_click():
    click_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO clicks (id, timestamp) VALUES (?, ?)", (click_id, timestamp))
        conn.commit()
    except Exception:
        raise HTTPException(400, "Invalid request")
    finally:
        conn.close()
    return None

@app.get("/clicks")
async def retrieve_clicks(date: str, direction: str):
    try:
        datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "Invalid date format")
    if direction not in ["<", ">", "<=", ">="]:
        raise HTTPException(400, "Invalid direction")
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    query = f"SELECT id, timestamp FROM clicks WHERE timestamp {direction} ?"
    cursor.execute(query, (date,))
    results = cursor.fetchall()
    conn.close()
    if not results:
        raise HTTPException(404, "No clicks found")
    return [
        {"id": row[0], "timestamp": row[1]}
        for row in results
    ]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)