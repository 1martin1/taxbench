import os
import uuid
import sqlite3
from datetime import datetime, date, time, timezone
from enum import Enum
from typing import List

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager

DB_PATH = "db.sqlite3"


def get_db_connection():
    """Create a new SQLite connection with row factory for dict‑like access."""
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize the SQLite database with the required table."""
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan hook to initialise the database on startup."""
    init_db()
    yield


app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks.",
    lifespan=lifespan,
)


class ClickResponse(BaseModel):
    id: str = Field(..., description="The unique identifier for the click.")
    timestamp: datetime = Field(..., description="The timestamp of the click.")


class Direction(str, Enum):
    """Allowed direction operators for filtering clicks."""
    LT = "<"
    LTE = "<="
    GT = ">"
    GTE = ">="


@app.post(
    "/click",
    status_code=status.HTTP_201_CREATED,
    response_model=ClickResponse,
    summary="Register a click",
    description="Endpoint to register a user's click in the database.",
)
def register_click() -> ClickResponse:
    """Create a new click record with a generated UUID and the current UTC timestamp."""
    click_id = str(uuid.uuid4())
    now = datetime.utcnow().replace(microsecond=0, tzinfo=timezone.utc)

    # Store timestamps as ISO‑8601 strings with a trailing 'Z' to denote UTC.
    timestamp_str = now.isoformat().replace("+00:00", "Z")

    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
            (click_id, timestamp_str),
        )
        conn.commit()

    # Return a naive datetime (without tzinfo) to match the original OpenAPI example.
    return ClickResponse(id=click_id, timestamp=now.replace(tzinfo=None))


@app.get(
    "/clicks",
    response_model=List[ClickResponse],
    summary="Retrieve clicks",
    description="Endpoint to retrieve clicks before or after a certain date.",
    responses={
        200: {"description": "A list of clicks"},
        400: {"description": "Invalid request"},
        404: {"description": "No clicks found"},
    },
)
def get_clicks(
    date: date = Query(
        ...,
        description="The date to filter clicks.",
        example="2022-01-01",
    ),
    direction: Direction = Query(
        ...,
        description=(
            "Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date."
        ),
    ),
) -> List[ClickResponse]:
    """
    Retrieve clicks filtered by a date and a direction operator.
    The `date` parameter is interpreted as a whole day in UTC.
    """
    # Determine the start and end of the requested day in UTC.
    start_dt = datetime.combine(date, time.min, tzinfo=timezone.utc).replace(microsecond=0)
    end_dt = datetime.combine(date, time.max, tzinfo=timezone.utc).replace(microsecond=0)

    # Map the enum value to the appropriate SQL operator and bound timestamp.
    if direction == Direction.LT:
        sql_op = "<"
        bound = start_dt.isoformat().replace("+00:00", "Z")
    elif direction == Direction.LTE:
        sql_op = "<="
        bound = end_dt.isoformat().replace("+00:00", "Z")
    elif direction == Direction.GT:
        sql_op = ">"
        bound = end_dt.isoformat().replace("+00:00", "Z")
    elif direction == Direction.GTE:
        sql_op = ">="
        bound = start_dt.isoformat().replace("+00:00", "Z")
    else:
        # This branch is unreachable because FastAPI validates the enum.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid direction parameter.",
        )

    query = f"""
        SELECT id, timestamp
        FROM clicks
        WHERE timestamp {sql_op} ?
        ORDER BY timestamp ASC
    """

    with get_db_connection() as conn:
        rows = conn.execute(query, (bound,)).fetchall()

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No clicks found for the given criteria.",
        )

    result: List[ClickResponse] = []
    for row in rows:
        ts_str: str = row["timestamp"]
        # Strip trailing 'Z' before parsing; treat as UTC.
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        ts_dt = datetime.fromisoformat(ts_str)
        # Return naive datetime to stay consistent with the response model.
        result.append(ClickResponse(id=row["id"], timestamp=ts_dt.replace(tzinfo=None)))

    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)