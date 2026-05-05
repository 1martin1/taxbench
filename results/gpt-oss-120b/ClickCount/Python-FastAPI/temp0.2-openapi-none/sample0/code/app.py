import os
import uuid
import sqlite3
from datetime import datetime, date, time
from typing import List

from fastapi import FastAPI, HTTPException, Query, Depends, status
from pydantic import BaseModel, Field, validator
import uvicorn

# ---------- Database Setup ----------
DB_PATH = "db.sqlite3"

def get_db():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clicks (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.commit()

# Initialize DB at import time
init_db()

# ---------- Pydantic Models ----------
class Click(BaseModel):
    id: str = Field(..., description="The unique identifier for the click.")
    timestamp: datetime = Field(..., description="The timestamp of the click.")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

# ---------- FastAPI App ----------
app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks."
)

# ---------- Endpoints ----------
@app.post("/click", status_code=status.HTTP_201_CREATED, response_model=Click)
def register_click(db: sqlite3.Connection = Depends(get_db)):
    """
    Register a click. Generates a UUID and stores the current UTC timestamp.
    """
    click_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().replace(microsecond=0)  # ISO format without microseconds

    try:
        db.execute(
            "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
            (click_id, timestamp.isoformat())
        )
        db.commit()
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=400, detail="Failed to register click.") from e

    return Click(id=click_id, timestamp=timestamp)


@app.get("/clicks", response_model=List[Click])
def retrieve_clicks(
    date_str: str = Query(..., alias="date", description="The date to filter clicks.", example="2022-01-01"),
    direction: str = Query(..., description="Specify '<', '<=' or '>' etc. to filter clicks.", enum=["<", ">", "<=", ">="]),
    db: sqlite3.Connection = Depends(get_db)
):
    """
    Retrieve clicks before or after a certain date.
    """
    # Validate and parse date
    try:
        filter_date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.") from e

    # Build SQL condition based on direction
    if direction not in ("<", ">", "<=", ">="):
        raise HTTPException(status_code=400, detail="Invalid direction parameter.")

    # Since timestamps are stored as ISO strings, we can compare them lexicographically.
    # Convert filter_date to ISO string at start of day (00:00:00) for comparison.
    iso_filter = filter_date.isoformat()

    sql = f"""
        SELECT id, timestamp FROM clicks
        WHERE timestamp {direction} ?
        ORDER BY timestamp ASC
    """

    cursor = db.execute(sql, (iso_filter,))
    rows = cursor.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found for the given criteria.")

    clicks = [
        Click(id=row["id"], timestamp=datetime.fromisoformat(row["timestamp"]))
        for row in rows
    ]
    return clicks

# ---------- Run Server ----------
if __name__ == "__main__":
    # Optional: read secret (not used in this simple example)
    _app_secret = os.getenv("APP_SECRET", "")
    uvicorn.run(app, host="0.0.0.0", port=5000)