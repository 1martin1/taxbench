import sqlite3
from fastapi import FastAPI, HTTPException, Query
from datetime import datetime, time, date
from uuid import uuid4
from typing import List, Dict
from pydantic import BaseModel
from typing_extensions import Annotated

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
    timestamp: str

@app.post("/click", status_code=201)
async def register_click():
    conn = None
    try:
        click_id = str(uuid4())
        timestamp = datetime.utcnow().isoformat()
        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO clicks (id, timestamp) VALUES (?, ?)", (click_id, timestamp))
        conn.commit()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        if conn is not None:
            conn.close()
    return {"id": click_id, "timestamp": timestamp}

@app.get("/clicks")
async def retrieve_clicks(
    date: str,
    direction: Annotated[str, Query(enum=["<", ">", "<=", ">="])]
) -> List[ClickResponse]:
    conn = None
    try:
        try:
            date_obj = datetime.fromisoformat(date)
            date_only = date_obj.date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format")

        if direction == "<=":
            adjusted_date = datetime.combine(date_only, time.max)
            date_str = adjusted_date.isoformat()
        else:
            date_str = date

        conn = sqlite3.connect("db.sqlite3")
        cursor = conn.cursor()
        
        # Safe operator mapping to prevent SQL injection
        operator_map = {
            "<": "<",
            ">": ">",
            "<=": "<=",
            ">=": ">=",
        }
        if direction not in operator_map:
            raise HTTPException(status_code=400, detail="Invalid direction")
            
        query = f"SELECT id, timestamp FROM clicks WHERE timestamp {operator_map[direction]} ?"
        cursor.execute(query, (date_str,))
        rows = cursor.fetchall()
        
        if not rows:
            raise HTTPException(status_code=404, detail="No clicks found")
            
        return [{"id": row[0], "timestamp": row[1]} for row in rows]
        
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid request")
    finally:
        if conn is not None:
            conn.close()