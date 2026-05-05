import sqlite3
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime, date
import uuid

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

class ClickResponse(BaseModel):
    id: str
    timestamp: datetime

@app.post("/click", status_code=201)
def register_click():
    click_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO clicks (id, timestamp) VALUES (?, ?)", (click_id, timestamp))
        conn.commit()
    finally:
        conn.close()
    return None

@app.get("/clicks", response_model=list[ClickResponse])
def retrieve_clicks(date: date = Query(..., description="The date to filter clicks."), direction: str = Query(..., description="Specify '<', '>', '<=', or '>=' to filter clicks.")):
    allowed_directions = {"<", ">", "<=", ">="}
    if direction not in allowed_directions:
        raise HTTPException(400, "Invalid direction")
    
    # Map input direction to safe SQL operator
    operator_map = {
        "<": "<",
        ">": ">",
        "<=": "<=",
        ">=": ">=",
    }
    operator = operator_map[direction]
    
    datetime_str = f"{date.isoformat()}T00:00:00"
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    query = f"SELECT id, timestamp FROM clicks WHERE timestamp {operator} ? ORDER BY timestamp LIMIT 1000"
    
    cursor.execute(query, (datetime_str,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        raise HTTPException(404, "No clicks found")
    
    clicks = []
    for row in rows:
        try:
            click = ClickResponse(id=row[0], timestamp=datetime.fromisoformat(row[1]))
            clicks.append(click)
        except ValueError:
            raise HTTPException(500, "Invalid timestamp in database")
    
    return clicks

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)