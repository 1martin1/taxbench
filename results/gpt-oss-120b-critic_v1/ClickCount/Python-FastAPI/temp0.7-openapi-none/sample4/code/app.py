import os
import uuid
import sqlite3
from datetime import datetime, timedelta
from enum import Enum
from typing import List

from fastapi import FastAPI, HTTPException, Query, Depends, status
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

# -------------------------------------------------
# Configuration
# -------------------------------------------------
DATABASE_PATH = "db.sqlite3"

# Load required secret; fail fast if not provided.
APP_SECRET = os.getenv("APP_SECRET")
if APP_SECRET is None:
    raise RuntimeError("Environment variable APP_SECRET must be set.")

# Limits to protect resources
MAX_TOTAL_CLICKS = 100_000          # Maximum rows allowed in the clicks table
MAX_RETURNED_CLICKS = 5_000        # Maximum rows returned by the GET endpoint

# -------------------------------------------------
# Database utilities
# -------------------------------------------------
def get_connection() -> sqlite3.Connection:
    """Create a new SQLite connection. Caller is responsible for closing."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    """Initialize the SQLite database with the required table."""
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clicks (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

def get_total_clicks(conn: sqlite3.Connection) -> int:
    """Return the total number of click records stored."""
    cur = conn.execute("SELECT COUNT(*) AS cnt FROM clicks")
    return cur.fetchone()["cnt"]

# -------------------------------------------------
# Pydantic models
# -------------------------------------------------
class Click(BaseModel):
    id: str = Field(..., description="The unique identifier for the click.")
    timestamp: str = Field(..., description="The timestamp of the click in ISO-8601 format.")

class Direction(str, Enum):
    lt = "<"
    gt = ">"
    lte = "<="
    gte = ">="

# -------------------------------------------------
# FastAPI app
# -------------------------------------------------
app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks."
)

@app.on_event("startup")
def on_startup():
    init_db()

# Dependency that provides a DB connection per request
def db_dep():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()

# -------------------------------------------------
# Endpoints
# -------------------------------------------------
@app.post("/click", status_code=status.HTTP_201_CREATED)
def register_click(db: sqlite3.Connection = Depends(db_dep)):
    """
    Register a click with a generated UUID and the current UTC timestamp.
    Enforces a maximum total number of stored clicks to prevent resource exhaustion.
    """
    if get_total_clicks(db) >= MAX_TOTAL_CLICKS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Click storage limit reached. Try again later."
        )

    click_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().replace(microsecond=0).isoformat()
    try:
        db.execute(
            "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
            (click_id, timestamp)
        )
        db.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail="Failed to register click.") from exc

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={"id": click_id, "timestamp": timestamp}
    )

@app.get("/clicks", response_model=List[Click])
def retrieve_clicks(
    date: str = Query(..., description="The date to filter clicks. Format: YYYY-MM-DD"),
    direction: Direction = Query(..., description="Specify '<', '<=' or '>' etc. to filter clicks."),
    db: sqlite3.Connection = Depends(db_dep)
):
    """
    Retrieve clicks filtered by a date and direction.
    Limits the number of returned rows to protect against excessive memory usage.
    """
    # Validate and parse the date
    try:
        date_obj = datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Use YYYY-MM-DD."
        ) from exc

    # Determine the comparison operator and the value to compare against
    if direction == Direction.lt:
        op = "<"
        compare_dt = date_obj
    elif direction == Direction.lte:
        op = "<="
        compare_dt = date_obj + timedelta(days=1) - timedelta(microseconds=1)
    elif direction == Direction.gt:
        op = ">"
        compare_dt = date_obj
    elif direction == Direction.gte:
        op = ">="
        compare_dt = date_obj
    else:
        # This branch should never be reached because FastAPI validates the enum
        raise HTTPException(status_code=400, detail="Invalid direction parameter.")

    compare_str = compare_dt.replace(microsecond=0).isoformat()

    query = f"""
        SELECT id, timestamp
        FROM clicks
        WHERE timestamp {op} ?
        ORDER BY timestamp ASC
        LIMIT ?
    """

    cursor = db.execute(query, (compare_str, MAX_RETURNED_CLICKS + 1))
    rows = cursor.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found for the given criteria.")

    if len(rows) > MAX_RETURNED_CLICKS:
        raise HTTPException(
            status_code=400,
            detail=f"Result set too large (>{MAX_RETURNED_CLICKS} records). Refine your query."
        )

    clicks = [Click(id=row["id"], timestamp=row["timestamp"]) for row in rows]
    return clicks

# -------------------------------------------------
# Run the server
# -------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000, log_level="info")