from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
import sqlite3
import uuid
from datetime import datetime
from typing import List
import os

app = FastAPI()

class Click(BaseModel):
    id: str
    timestamp: datetime

@app.on_event("startup")
def create_tables():
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS clicks (
                id TEXT PRIMARY KEY,
                timestamp DATETIME NOT NULL
            )
        ''')
        conn.commit()
    except Exception as e:
        raise RuntimeError("Failed to initialize database") from e
    finally:
        conn.close()

@app.post("/click", status_code=201)
async def register_click():
    try:
        click_id = str(uuid.uuid4())
        timestamp = datetime.now(datetime.timezone.utc).isoformat()
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO clicks (id, timestamp) VALUES (?, ?)", (click_id, timestamp))
        conn.commit()
        return {"id": click_id, "timestamp": timestamp}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e
    finally:
        conn.close()

@app.get("/clicks", response_model=List[Click])
async def retrieve_clicks(
    date: str = Query(..., description="The date to filter clicks."),
    direction: str = Query(..., description="Specify '<', '>', '<=' or '>=' to filter clicks.", enum=["<", ">", "<=", ">="])
):
    try:
        date_obj = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    date_str = f"{date}T00:00:00Z"
    
    try:
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        query = f"SELECT id, timestamp FROM clicks WHERE timestamp {direction} ?"
        cursor.execute(query, (date_str,))
        rows = cursor.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error") from e
    finally:
        conn.close()

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found")

    clicks = []
    for click_id, timestamp_str in rows:
        timestamp = datetime.fromisoformat(timestamp_str)
        clicks.append(Click(id=click_id, timestamp=timestamp))

    return clicks

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)