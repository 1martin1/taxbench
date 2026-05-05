import sqlite3
from fastapi import FastAPI, Query, HTTPException
from datetime import datetime, date
import uuid

app = FastAPI()

@app.on_event("startup")
async def create_tables():
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
    try:
        click_id = str(uuid.uuid4())
        timestamp = datetime.now(datetime.timezone.utc).isoformat()
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO clicks (id, timestamp) VALUES (?, ?)", (click_id, timestamp))
        conn.commit()
        conn.close()
        return None
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/clicks")
async def retrieve_clicks(date: date = Query(...), direction: str = Query(..., enum=["<", ">", "<=", ">="])):
    allowed_directions = {"<", ">", "<=", ">="}
    if direction not in allowed_directions:
        raise HTTPException(status_code=400, detail="Invalid direction")
    try:
        datetime_str = f"{date.isoformat()}T00:00:00Z"
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute(f"SELECT id, timestamp FROM clicks WHERE timestamp {direction} ? ORDER BY timestamp LIMIT 1000", (datetime_str,))
        results = cursor.fetchall()
        conn.close()
        if not results:
            raise HTTPException(status_code=404, detail="No clicks found")
        return [{"id": row[0], "timestamp": row[1]} for row in results]
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)