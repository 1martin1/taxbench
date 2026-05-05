import os
import uuid
import sqlite3
from datetime import datetime, date, time, timezone
from enum import Enum

from fastapi import FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
DB_PATH = "db.sqlite3"

# ----------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------
app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks.",
)

# ----------------------------------------------------------------------
# Enums and Schemas
# ----------------------------------------------------------------------
class Direction(str, Enum):
    lt = "<"
    gt = ">"
    lte = "<="
    gte = ">="


class ClickResponse(BaseModel):
    id: str = Field(..., description="The unique identifier for the click.")
    timestamp: str = Field(
        ...,
        description="The timestamp of the click.",
        example="2023-09-01T12:34:56Z",
    )


# ----------------------------------------------------------------------
# Database utilities
# ----------------------------------------------------------------------
def get_db_connection() -> sqlite3.Connection:
    """
    Returns a new SQLite connection with appropriate settings.
    """
    conn = sqlite3.connect(
        DB_PATH,
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False,  # allow usage from FastAPI's thread pool
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """
    Creates the clicks table if it does not already exist.
    """
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
def on_startup() -> None:
    init_db()


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post(
    "/click",
    status_code=status.HTTP_201_CREATED,
    summary="Register a click",
    responses={201: {"description": "Click registered successfully"}},
)
def register_click() -> Response:
    """
    Register a click. Generates a unique ID and stores the current UTC timestamp.
    Returns an empty body with HTTP 201 status as defined in the OpenAPI spec.
    """
    click_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).replace(microsecond=0)
    timestamp_iso = now.isoformat().replace("+00:00", "Z")

    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
                (click_id, timestamp_iso),
            )
            conn.commit()
    except sqlite3.IntegrityError as exc:
        # This should be extremely rare (UUID collision) but handle gracefully
        raise HTTPException(status_code=400, detail="Failed to register click.") from exc

    # No body is returned, only the status code
    return Response(status_code=status.HTTP_201_CREATED)


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
    date: str = Query(..., description="The date to filter clicks.", example="2022-01-01"),
    direction: Direction = Query(
        ...,
        description=(
            "Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date."
        ),
    ),
) -> list[ClickResponse]:
    """
    Retrieve clicks filtered by a date and direction.
    """
    # ------------------------------------------------------------------
    # Validate and parse the date parameter
    # ------------------------------------------------------------------
    try:
        filter_date: date = date.fromisoformat(date)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Invalid date format. Expected YYYY-MM-DD."
        ) from exc

    # ------------------------------------------------------------------
    # Build the SQL comparison based on the direction
    # ------------------------------------------------------------------
    if direction == Direction.lt:
        compare_ts = datetime.combine(filter_date, time.min, tzinfo=timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        sql_op = "<"
    elif direction == Direction.lte:
        compare_ts = datetime.combine(filter_date, time.max, tzinfo=timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        sql_op = "<="
    elif direction == Direction.gt:
        compare_ts = datetime.combine(filter_date, time.min, tzinfo=timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        sql_op = ">"
    elif direction == Direction.gte:
        compare_ts = datetime.combine(filter_date, time.min, tzinfo=timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        sql_op = ">="
    else:
        # This branch should never be hit because FastAPI validates the enum
        raise HTTPException(status_code=400, detail="Invalid direction parameter.")

    query = f"SELECT id, timestamp FROM clicks WHERE timestamp {sql_op} ? ORDER BY timestamp ASC"

    with get_db_connection() as conn:
        rows = conn.execute(query, (compare_ts,)).fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found for the given criteria.")

    return [ClickResponse(id=row["id"], timestamp=row["timestamp"]) for row in rows]


# ----------------------------------------------------------------------
# Run the application
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)