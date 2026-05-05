import sqlite3
import uuid
import datetime
from fastapi import FastAPI, HTTPException, Response

app = FastAPI()

MAX_CLICKS_RETURNED = 1000

@app.on_event("startup")
def create_table():
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
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO clicks (id, timestamp) VALUES (?, ?)", (click_id, timestamp))
        conn.commit()
        conn.close()
        return Response(status_code=201)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid request")

@app.get("/clicks")
async def retrieve_clicks(date: str, direction: str):
    if direction not in ["<", ">", "<=", ">="]:
        raise HTTPException(status_code=400, detail="Invalid direction")
    try:
        click_date = datetime.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    
    adjusted_date_str = date
    if direction in ['<=', '>=']:
        if direction == '<=':
            end_of_day = datetime.datetime.combine(click_date, datetime.time(23, 59, 59, 999999))
            adjusted_date_str = end_of_day.isoformat()
        elif direction == '>=':
            start_of_day = datetime.datetime.combine(click_date, datetime.time(0, 0))
            adjusted_date_str = start_of_day.isoformat()
    
    query = f"SELECT id, timestamp FROM clicks WHERE timestamp {direction} ? LIMIT {MAX_CLICKS_RETURNED}"
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    try:
        cursor.execute(query, (adjusted_date_str,))
        results = cursor.fetchall()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        conn.close()
    
    if not results:
        raise HTTPException(status_code=404, detail="No clicks found")
    return [ {"id": row[0], "timestamp": row[1]} for row in results ]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)