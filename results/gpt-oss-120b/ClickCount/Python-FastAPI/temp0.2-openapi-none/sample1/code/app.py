import os
import uuid
import sqlite3
from datetime import datetime, date, time
from typing import List

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field

app = FastAPI(title="Click Tracking API", version="1.0.0")

DB_PATH = "db.sqlite3"


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


class ClickResponse(BaseModel):
    id: str = Field(..., description="The unique identifier for the click.")
    timestamp: datetime = Field(..., description="The timestamp of the click.")


@app.post(
    "/click",
    status_code=status.HTTP_201_CREATED,
    response_model=ClickResponse,
    summary="Register a click",
    description="Endpoint to register a user's click in the database.",
)
def register_click():
    click_id = str(uuid.uuid4())
    now = datetime.utcnow().replace(microsecond=0)
    timestamp_str = now.isoformat() + "Z"  # explicit UTC marker

    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
            (click_id, timestamp_str),
        )
        conn.commit()

    return ClickResponse(id=click_id, timestamp=now)


@app.get(
    "/clicks",
    response_model=List[ClickResponse],
    summary="Retrieve clicks",
    description="Endpoint to retrieve clicks before or after a certain date.",
)
def get_clicks(
    date_str: str = Query(
        ...,
        description="The date to filter clicks.",
        example="2022-01-01",
    ),
    direction: str = Query(
        ...,
        description=(
            "Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date."
        ),
        regex="^(<|<=|>|>=)$",
    ),
):
    # Validate and parse date
    try:
        filter_date: date = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Expected YYYY-MM-DD.",
        )

    # Determine comparison bounds
    start_dt = datetime.combine(filter_date, time.min).replace(microsecond=0)
    end_dt = datetime.combine(filter_date, time.max).replace(microsecond=0)

    # Map direction to SQL operator and bound value
    if direction == "<":
        sql_op = "<"
        bound = start_dt.isoformat() + "Z"
    elif direction == "<=":
        sql_op = "<="
        bound = end_dt.isoformat() + "Z"
    elif direction == ">":
        sql_op = ">"
        bound = end_dt.isoformat() + "Z"
    elif direction == ">=":
        sql_op = ">="
        bound = start_dt.isoformat() + "Z"
    else:
        # Should never happen because of regex validation
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid direction parameter.",
        )

    query = f"SELECT id, timestamp FROM clicks WHERE timestamp {sql_op} ? ORDER BY timestamp ASC"

    with get_db_connection() as conn:
        rows = conn.execute(query, (bound,)).fetchall()

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No clicks found for the given criteria.",
        )

    result = []
    for row in rows:
        # Parse stored timestamp back to datetime
        ts_str = row["timestamp"]
        # Remove trailing Z if present for parsing
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1]
        ts_dt = datetime.fromisoformat(ts_str)
        result.append(ClickResponse(id=row["id"], timestamp=ts_dt))

    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)