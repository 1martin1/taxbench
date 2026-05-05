import os
import uuid
import sqlite3
from datetime import datetime, date, time, timedelta
from enum import Enum

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field
import uvicorn

# ---------- Database Setup ----------
DB_PATH = "db.sqlite3"

def get_connection():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clicks (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.commit()

init_db()

# ---------- Models ----------
class Direction(str, Enum):
    lt = "<"
    gt = ">"
    lte = "<="
    gte = ">="

class ClickResponse(BaseModel):
    id: str = Field(..., description="The unique identifier for the click.")
    timestamp: datetime = Field(..., description="The timestamp of the click.")

# ---------- FastAPI App ----------
app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks."
)

# ---------- Endpoints ----------
@app.post("/click", status_code=status.HTTP_201_CREATED, response_model=ClickResponse)
def register_click():
    """
    Register a click. Generates a UUID and stores the current UTC timestamp.
    """
    click_id = str(uuid.uuid4())
    now = datetime.utcnow().replace(microsecond=0)  # ISO format without microseconds

    with get_connection() as conn:
        try:
            conn.execute(
                "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
                (click_id, now.isoformat())
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Could not register click.")

    return ClickResponse(id=click_id, timestamp=now)


@app.get("/clicks", response_model=list[ClickResponse])
def retrieve_clicks(
    date_str: str = Query(..., alias="date", description="The date to filter clicks.", example="2022-01-01"),
    direction: Direction = Query(..., description="Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date.")
):
    """
    Retrieve clicks filtered by a date and direction.
    """
    # Parse the date string (expected format YYYY-MM-DD)
    try:
        filter_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    # Build datetime boundaries based on direction
    # For simplicity we compare full timestamps against the start of the day (00:00:00) or end of the day (23:59:59)
    start_of_day = datetime.combine(filter_date, time.min)  # 00:00:00
    end_of_day = datetime.combine(filter_date, time.max)    # 23:59:59.999999

    if direction == Direction.lt:
        sql = "SELECT id, timestamp FROM clicks WHERE timestamp < ?"
        param = start_of_day.isoformat()
    elif direction == Direction.lte:
        sql = "SELECT id, timestamp FROM clicks WHERE timestamp <= ?"
        param = end_of_day.isoformat()
    elif direction == Direction.gt:
        sql = "SELECT id, timestamp FROM clicks WHERE timestamp > ?"
        param = end_of_day.isoformat()
    elif direction == Direction.gte:
        sql = "SELECT id, timestamp FROM clicks WHERE timestamp >= ?"
        param = start_of_day.isoformat()
    else:
        raise HTTPException(status_code=400, detail="Invalid direction.")

    with get_connection() as conn:
        rows = conn.execute(sql, (param,)).fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found for the given criteria.")

    result = [
        ClickResponse(
            id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"])
        )
        for row in rows
    ]
    return result

# ---------- Run Server ----------
if __name__ == "__main__":
    # Optional: use APP_SECRET for something in the future
    _ = os.getenv("APP_SECRET")
    uvicorn.run(app, host="0.0.0.0", port=5000)