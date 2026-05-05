import os
import uuid
import sqlite3
from datetime import datetime, date, time, timezone
from enum import Enum
from typing import List, Generator

from fastapi import FastAPI, HTTPException, Query, status, Depends
from pydantic import BaseModel, Field

# ---------- Database utilities ----------
DB_PATH = "db.sqlite3"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
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


# ---------- Pydantic models ----------
class Click(BaseModel):
    id: str = Field(..., description="The unique identifier for the click.")
    timestamp: datetime = Field(..., description="The timestamp of the click.")


class Direction(str, Enum):
    lt = "<"
    gt = ">"
    le = "<="
    ge = ">="

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if v not in ("<", ">", "<=", ">="):
            raise ValueError("Invalid direction")
        return v


# ---------- FastAPI app ----------
app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks.",
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# Dependency that provides a DB connection per request
def db_conn() -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


# ---------- Endpoints ----------
@app.post(
    "/click",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Click registered successfully"},
        400: {"description": "Invalid request"},
    },
)
def register_click(conn: sqlite3.Connection = Depends(db_conn)):
    """
    Register a click. Generates a UUID and stores the current UTC timestamp.
    """
    click_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
            (click_id, now),
        )
        conn.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Database error")
    return {"id": click_id, "timestamp": now}


@app.get(
    "/clicks",
    response_model=List[Click],
    responses={
        200: {"description": "A list of clicks"},
        400: {"description": "Invalid request"},
        404: {"description": "No clicks found"},
    },
)
def get_clicks(
    date: str = Query(..., description="The date to filter clicks.", example="2022-01-01"),
    direction: Direction = Query(..., description="Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date."),
    conn: sqlite3.Connection = Depends(db_conn),
):
    """
    Retrieve clicks before or after a certain date.
    """
    # Validate date format
    try:
        filter_date: date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format, expected YYYY-MM-DD")

    # Convert date to datetime at midnight UTC for comparison
    filter_dt = datetime.combine(filter_date, time.min, tzinfo=timezone.utc)

    # Build SQL query based on direction
    op_map = {
        "<": "<",
        "<=": "<=",
        ">": ">",
        ">=": ">=",
    }
    sql_op = op_map[direction.value]

    query = f"""
        SELECT id, timestamp
        FROM clicks
        WHERE timestamp {sql_op} ?
        ORDER BY timestamp ASC
        LIMIT ?
    """

    MAX_ROWS = 1000  # safeguard against excessive result sets

    try:
        rows = conn.execute(query, (filter_dt.isoformat(), MAX_ROWS)).fetchall()
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Database query error")

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found")

    result = [
        Click(
            id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"]).astimezone(timezone.utc),
        )
        for row in rows
    ]

    return result


# ---------- Run the server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000, log_level="info")