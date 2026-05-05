import sqlite3
from fastapi import FastAPI, HTTPException, Query
from datetime import datetime, date, timezone
from uuid import uuid4
from typing import List, Dict
import os

app = FastAPI()

@app.on_event("startup")
def startup():
    app.db = sqlite3.connect('db.sqlite3', check_same_thread=False)
    app.db.execute("""
        CREATE TABLE IF NOT EXISTS clicks (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL
        )
    """)
    app.db.commit()

@app.on_event("shutdown")
def shutdown():
    app.db.close()

@app.post("/click", status_code=201)
async def register_click():
    click_id = str(uuid4())
    timestamp = datetime.utcnow().isoformat()
    try:
        app.db.execute("INSERT INTO clicks (id, timestamp) VALUES (?, ?)", (click_id, timestamp))
        app.db.commit()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid request")
    return {"id": click_id, "timestamp": timestamp}

@app.get("/clicks")
async def retrieve_clicks(
    date: str = Query(...),
    direction: str = Query(..., enum=["<", ">", "<=", ">="])
):
    try:
        date_obj = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    
    datetime_str = datetime.combine(date_obj, datetime.min.time()).isoformat()
    
    query = f"SELECT id, timestamp FROM clicks WHERE timestamp {direction} ?"
    cursor = app.db.execute(query, (datetime_str,))
    results = cursor.fetchall()
    
    if not results:
        raise HTTPException(status_code=404, detail="No clicks found")
    
    return [{"id": row[0], "timestamp": row[1]} for row in results]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)