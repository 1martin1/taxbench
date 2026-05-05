import sqlite3
from fastapi import FastAPI, HTTPException, Query
from datetime import datetime, date, time
import uuid
from enum import Enum

app = FastAPI()

def init_db():
    with sqlite3.connect('db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS clicks (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL
            )
        ''')
        conn.commit()

init_db()

class Direction(str, Enum):
    less = "<"
    greater = ">"
    less_equal = "<="
    greater_equal = ">="

@app.post("/click", status_code=201)
async def register_click():
    click_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()
    with sqlite3.connect('db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO clicks (id, timestamp) VALUES (?, ?)', (click_id, timestamp))
    return {"id": click_id, "timestamp": timestamp}

@app.get("/clicks")
async def retrieve_clicks(date: str = Query(...), direction: Direction = Query(...)):
    try:
        date_obj = date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    
    datetime_str = datetime.combine(date_obj, time.min).isoformat()
    allowed_ops = {'<', '>', '<=', '>='}
    
    if direction.value not in allowed_ops:
        raise HTTPException(status_code=400, detail="Invalid direction operator")
    
    with sqlite3.connect('db.sqlite3') as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT id, timestamp 
            FROM clicks 
            WHERE timestamp {direction.value} ? 
            ORDER BY timestamp 
            LIMIT 100
        """, (datetime_str,))
        rows = cursor.fetchall()
    
    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found")
    
    return [{"id": row[0], "timestamp": row[1]} for row in rows]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)