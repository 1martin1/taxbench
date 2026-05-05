import sqlite3
import uuid
from datetime import datetime, date, time, timezone
from fastapi import FastAPI, HTTPException, Response

app = FastAPI()

@app.on_event("startup")
def create_tables():
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clicks (
            id TEXT PRIMARY KEY,
            timestamp DATETIME NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

@app.post("/click", status_code=201)
async def register_click():
    try:
        click_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO clicks (id, timestamp) VALUES (?, ?)", (click_id, timestamp))
        conn.commit()
        conn.close()
        return Response(status_code=201)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid request")

@app.get("/clicks")
async def retrieve_clicks(date: str, direction: str):
    allowed_directions = ["<", ">", "<=", ">="]
    if direction not in allowed_directions:
        raise HTTPException(status_code=400, detail="Invalid direction")
    
    try:
        date_obj = date.fromisoformat(date)
        time_obj = time.min
        naive_datetime = datetime.combine(date_obj, time_obj)
        date_datetime = naive_datetime.replace(tzinfo=timezone.utc)
        date_str = date_datetime.isoformat()
        
        conn = sqlite3.connect('db.sqlite3')
        cursor = conn.cursor()
        cursor.execute(f"SELECT id, timestamp FROM clicks WHERE timestamp {direction} ?", (date_str,))
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            raise HTTPException(status_code=404, detail="No clicks found")
        
        clicks = [{"id": row[0], "timestamp": row[1]} for row in rows]
        return clicks
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid date format")
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid request")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)