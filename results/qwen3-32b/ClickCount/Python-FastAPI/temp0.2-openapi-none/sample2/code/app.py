import sqlite3
import uuid
from fastapi import FastAPI, HTTPException
from datetime import datetime
import uvicorn

app = FastAPI()

# Database setup
conn = sqlite3.connect('db.sqlite3', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS clicks (
        id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL
    )
''')
conn.commit()

@app.on_event("startup")
def on_startup():
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clicks (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL
        )
    ''')
    conn.commit()

@app.on_event("shutdown")
def on_shutdown():
    conn.close()

@app.post("/click", status_code=201)
def register_click():
    click_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat() + "Z"
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO clicks (id, timestamp) VALUES (?, ?)", (click_id, timestamp))
        conn.commit()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid request")
    return None

@app.get("/clicks")
def retrieve_clicks(date: str, direction: str):
    try:
        input_date = datetime.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    if direction not in ["<", ">", "<=", ">="]:
        raise HTTPException(status_code=400, detail="Invalid direction")
    compare_datetime = f"{input_date.isoformat()}T00:00:00Z"
    query = f"SELECT id, timestamp FROM clicks WHERE timestamp {direction} ?"
    cursor = conn.cursor()
    cursor.execute(query, (compare_datetime,))
    results = cursor.fetchall()
    if not results:
        raise HTTPException(status_code=404, detail="No clicks found")
    return [{"id": row[0], "timestamp": row[1]} for row in results]

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)