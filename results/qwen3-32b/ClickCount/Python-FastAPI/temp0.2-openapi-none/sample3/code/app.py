import sqlite3
import uuid
import datetime
from fastapi import FastAPI, HTTPException

app = FastAPI()

def init_db():
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clicks (
            id TEXT PRIMARY KEY,
            timestamp TEXT
        )
    """)
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

@app.post("/click", status_code=201)
async def create_click():
    click_id = str(uuid.uuid4())
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO clicks (id, timestamp) VALUES (?, ?)", (click_id, timestamp))
    conn.commit()
    conn.close()
    return

@app.get("/clicks")
async def get_clicks(date: str, direction: str):
    if direction not in ["<", ">", "<=", ">="]:
        raise HTTPException(status_code=400, detail="Invalid direction")
    try:
        date_obj = datetime.date.fromisoformat(date)
        date_dt = datetime.datetime.combine(date_obj, datetime.time.min, tzinfo=datetime.timezone.utc)
        date_str = date_dt.isoformat()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    query = "SELECT id, timestamp FROM clicks WHERE timestamp {} ?".format(direction)
    conn = sqlite3.connect("db.sqlite3")
    cursor = conn.cursor()
    cursor.execute(query, (date_str,))
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found")
    return [{"id": row[0], "timestamp": row[1]} for row in rows]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)