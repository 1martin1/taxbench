import os
import uuid
import sqlite3
from datetime import datetime
from typing import List

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field, validator
import uvicorn

# ---------- Constants ----------
DB_PATH = "db.sqlite3"
MAX_RESULTS = 1000  # Prevent excessive memory usage

# ---------- Database Setup ----------
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

init_db()

# ---------- Pydantic Models ----------
class Click(BaseModel):
    id: str = Field(..., description="The unique identifier for the click.")
    timestamp: datetime = Field(..., description="The timestamp of the click.")

    @validator("timestamp", pre=True)
    def parse_timestamp(cls, v):
        """
        Accept ISO‑8601 strings with a trailing 'Z' (UTC) or offset.
        """
        if isinstance(v, str):
            # Replace trailing Z with +00:00 to make it compatible with fromisoformat
            if v.endswith("Z"):
                v = v[:-1] + "+00:00"
            return datetime.fromisoformat(v)
        return v

# ---------- FastAPI App ----------
app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks."
)

# ---------- Endpoints ----------
@app.post(
    "/click",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Click registered successfully"},
        400: {"description": "Invalid request"},
    },
)
def register_click():
    """
    Register a click with a generated UUID and the current UTC timestamp.
    """
    click_id = str(uuid.uuid4())
    # Store timestamp in ISO‑8601 UTC with trailing 'Z' for consistency
    timestamp = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
                (click_id, timestamp),
            )
            conn.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Database error")

    return {"id": click_id, "timestamp": timestamp}


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
    """
    Retrieve clicks filtered by a date and direction.
    """
    # Validate date format (YYYY-MM-DD)
    try:
        filter_date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid date format, expected YYYY-MM-DD"
        )

    # Convert to ISO string for comparison (midnight UTC) with trailing Z
    iso_filter = filter_date.isoformat() + "Z"

    # Ensure the direction is one of the allowed operators (double‑check)
    if direction not in {"<", "<=", ">", ">="}:
        raise HTTPException(status_code=400, detail="Invalid direction operator")

    # Build safe SQL query using the validated operator and limit results
    sql = f"""
        SELECT id, timestamp
        FROM clicks
        WHERE timestamp {direction} ?
        ORDER BY timestamp ASC
        LIMIT ?
    """

    with get_connection() as conn:
        cursor = conn.execute(sql, (iso_filter, MAX_RESULTS))
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found")

    clicks = [Click(id=row["id"], timestamp=row["timestamp"]) for row in rows]
    return clicks


# ---------- Run Server ----------
if __name__ == "__main__":
    # The secret is not used in this simple application but read to avoid unused‑env warnings.
    _ = os.getenv("APP_SECRET")
    uvicorn.run(app, host="0.0.0.0", port=5000)