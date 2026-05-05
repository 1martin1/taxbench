import sqlite3
from fastapi import FastAPI, Query, HTTPException
from datetime import datetime
import uuid
import os

app = FastAPI()

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clicks (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

@app.post("/click", status_code=201)
async def register_click():
    click_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO clicks (id, timestamp) VALUES (?, ?)", (click_id, timestamp))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid request") from e
    finally:
        conn.close()
    return {"id": click_id, "timestamp": timestamp}

@app.get("/clicks")
async def retrieve_clicks(date: str = Query(...), direction: str = Query(..., enum=["<", ">", "<=", ">="])):
    try:
        date_obj = datetime.fromisoformat(date)
        date_str = date_obj.date().isoformat()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT id, timestamp FROM clicks WHERE timestamp {direction} ?", (date_str,))
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid request") from e
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found")

    return [{"id": row[0], "timestamp": row[1]} for row in rows]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)