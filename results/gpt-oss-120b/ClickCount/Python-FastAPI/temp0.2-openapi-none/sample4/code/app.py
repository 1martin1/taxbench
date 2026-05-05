import os
import uuid
import sqlite3
from datetime import datetime, date, time, timezone
from enum import Enum

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field

app = FastAPI(title="Click Tracking API", version="1.0.0", description="API for registering and retrieving user clicks.")

DB_PATH = "db.sqlite3"


class Direction(str, Enum):
    lt = "<"
    gt = ">"
    lte = "<="
    gte = ">="


class ClickResponse(BaseModel):
    id: str = Field(..., description="The unique identifier for the click.")
    timestamp: str = Field(..., description="The timestamp of the click.", example="2023-09-01T12:34:56Z")


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clicks (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.commit()


@app.on_event("startup")
def on_startup():
    init_db()


@app.post("/click", status_code=status.HTTP_201_CREATED, response_model=ClickResponse, summary="Register a click")
def register_click():
    """
    Register a click. Generates a unique ID and stores the current UTC timestamp.
    """
    click_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).replace(microsecond=0)
    timestamp_iso = now.isoformat().replace("+00:00", "Z")

    with get_db_connection() as conn:
        try:
            conn.execute(
                "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
                (click_id, timestamp_iso),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Failed to register click.")

    return ClickResponse(id=click_id, timestamp=timestamp_iso)


@app.get(
    "/clicks",
    response_model=list[ClickResponse],
    summary="Retrieve clicks",
    responses={
        200: {"description": "A list of clicks"},
        400: {"description": "Invalid request"},
        404: {"description": "No clicks found"},
    },
)
def get_clicks(
    date_str: str = Query(..., description="The date to filter clicks.", example="2022-01-01"),
    direction: Direction = Query(..., description="Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date."),
):
    """
    Retrieve clicks filtered by a date and direction.
    """
    # Validate and parse date
    try:
        filter_date: date = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD.")

    # Determine comparison timestamp(s)
    if direction == Direction.lt:
        compare_ts = datetime.combine(filter_date, time.min, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        sql_op = "<"
        param = compare_ts
    elif direction == Direction.lte:
        compare_ts = datetime.combine(filter_date, time.max, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        sql_op = "<="
        param = compare_ts
    elif direction == Direction.gt:
        compare_ts = datetime.combine(filter_date, time.min, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        sql_op = ">"
        param = compare_ts
    elif direction == Direction.gte:
        compare_ts = datetime.combine(filter_date, time.min, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        sql_op = ">="
        param = compare_ts
    else:
        raise HTTPException(status_code=400, detail="Invalid direction parameter.")

    query = f"SELECT id, timestamp FROM clicks WHERE timestamp {sql_op} ? ORDER BY timestamp ASC"

    with get_db_connection() as conn:
        rows = conn.execute(query, (param,)).fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found for the given criteria.")

    result = [ClickResponse(id=row["id"], timestamp=row["timestamp"]) for row in rows]
    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)