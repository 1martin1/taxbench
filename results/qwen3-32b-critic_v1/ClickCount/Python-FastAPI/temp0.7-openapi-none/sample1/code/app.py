from fastapi import FastAPI, HTTPException, Query
from datetime import datetime, timezone
import sqlite3
import uuid
from typing import List, Dict

app = FastAPI()

@app.on_event("startup")
async def startup():
    try:
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clicks (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL
            )
        """)
        conn.commit()
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database initialization failed")
    finally:
        conn.close()

MAX_RESULTS = 1000

@app.post("/click", status_code=201)
async def register_click():
    try:
        click_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO clicks (id, timestamp) VALUES (?, ?)", (click_id, timestamp))
        conn.commit()
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()
    return

@app.get("/clicks")
async def get_clicks(
    date: str = Query(..., description="The date to filter clicks.", example="2022-01-01"),
    direction: str = Query(..., description="Specify '<', '>', '<=', or '>=' to filter clicks.", enum=["<", ">", "<=", ">="])
):
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    
    try:
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        query = f"SELECT id, timestamp FROM clicks WHERE DATE(timestamp) {direction} DATE(?) LIMIT {MAX_RESULTS}"
        cursor.execute(query, (date,))
        results = cursor.fetchall()
    except sqlite3.Error as e:
        conn.close()
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        conn.close()
    
    if not results:
        raise HTTPException(status_code=404, detail="No clicks found")
    
    return [{"id": row[0], "timestamp": row[1]} for row in results]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)