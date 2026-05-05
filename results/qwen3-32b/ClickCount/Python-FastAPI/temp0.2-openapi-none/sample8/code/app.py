import sqlite3
from fastapi import FastAPI, Query, HTTPException
from datetime import datetime, timezone, time, timedelta
from uuid import uuid4

app = FastAPI()

def init_db():
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS clicks (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

@app.post("/click", status_code=201)
def register_click():
    click_id = str(uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO clicks (id, timestamp) VALUES (?, ?)", (click_id, timestamp))
        conn.commit()
    except Exception:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        conn.close()
    return None

@app.get("/clicks")
def retrieve_clicks(date: str = Query(...), direction: str = Query(..., enum=["<", ">", "<=", ">="])):
    try:
        date_obj = datetime.strptime(date, '%Y-%m-%d').date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    
    start_of_day = datetime.combine(date_obj, time.min, tzinfo=timezone.utc)
    end_of_day = datetime.combine(date_obj, time.max, tzinfo=timezone.utc)
    
    if direction not in ['<', '>', '<=', '>=']:
        raise HTTPException(status_code=400, detail="Invalid direction")
    
    if direction == '>':
        value = start_of_day.isoformat()
        condition = "timestamp > ?"
    elif direction == '>=':
        value = start_of_day.isoformat()
        condition = "timestamp >= ?"
    elif direction == '<':
        value = start_of_day.isoformat()
        condition = "timestamp < ?"
    elif direction == '<=':
        value = end_of_day.isoformat()
        condition = "timestamp <= ?"
    else:
        raise HTTPException(status_code=400, detail="Invalid direction")
    
    conn = sqlite3.connect('db.sqlite3')
    c = conn.cursor()
    try:
        c.execute(f"SELECT id, timestamp FROM clicks WHERE {condition}", (value,))
        rows = c.fetchall()
    finally:
        conn.close()
    
    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found")
    
    clicks = [{"id": row[0], "timestamp": row[1]} for row in rows]
    return clicks

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)