import os
import uuid
import datetime
import sqlite3
import time
from typing import List

from fastapi import FastAPI, HTTPException, Query, status, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

# ---------- FastAPI app ----------
app = FastAPI(title="Click Tracking API", version="1.0.0")

# ---------- Constants ----------
DB_PATH = "db.sqlite3"
MAX_ROWS_RETURNED = 1000  # safeguard against unbounded result sets
RATE_LIMIT_MAX_REQUESTS = 20  # max requests per window per IP
RATE_LIMIT_WINDOW_SECONDS = 60  # time window in seconds


# ---------- Pydantic models ----------
class ClickResponse(BaseModel):
    id: str
    timestamp: str = Field(..., description="ISO‑8601 UTC timestamp of the click")


# ---------- Database utilities ----------
def init_db() -> None:
    """Create the clicks table if it does not exist."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS clicks (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def get_db():
    """FastAPI dependency that provides a fresh SQLite connection per request."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ---------- Rate limiting middleware ----------
class RateLimiterMiddleware(BaseHTTPMiddleware):
    """
    Simple in‑memory rate limiter. Limits the number of requests per IP address
    within a sliding time window.
    """

    def __init__(self, app, max_requests: int = RATE_LIMIT_MAX_REQUESTS, window: int = RATE_LIMIT_WINDOW_SECONDS):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window
        self.clients = {}  # client_ip -> list[timestamps]

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "anonymous"
        now = time.time()
        timestamps = self.clients.get(client_ip, [])
        # Keep only timestamps within the window
        timestamps = [t for t in timestamps if now - t < self.window]

        if len(timestamps) >= self.max_requests:
            return Response(content="Too Many Requests", status_code=429)

        timestamps.append(now)
        self.clients[client_ip] = timestamps
        response = await call_next(request)
        return response


app.add_middleware(RateLimiterMiddleware)


# ---------- Startup / shutdown ----------
@app.on_event("startup")
def on_startup():
    # Ensure the database file and schema exist
    init_db()


# ---------- Endpoints ----------
@app.post(
    "/click",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Click registered successfully"},
        400: {"description": "Invalid request"},
    },
)
def register_click(db: sqlite3.Connection = Depends(get_db)):
    """
    Register a click. Generates a UUID and stores the current UTC timestamp.
    No request body is required, matching the OpenAPI specification.
    """
    click_id = str(uuid.uuid4())
    timestamp = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    try:
        db.execute(
            "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
            (click_id, timestamp),
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Click with this id already exists.",
        )

    return {"id": click_id, "timestamp": timestamp}


@app.get(
    "/clicks",
    response_model=List[ClickResponse],
    responses={
        200: {"description": "A list of clicks"},
        400: {"description": "Invalid request"},
        404: {"description": "No clicks found"},
    },
)
def retrieve_clicks(
    date: str = Query(..., description="The date to filter clicks (YYYY-MM-DD)"),
    direction: str = Query(
        ...,
        description=(
            "Specify '<', '<=' , '>' or '>=' to filter clicks before, before-or-equal, "
            "after, or after-or-equal the given date."
        ),
        regex="^(<|<=|>|>=)$",
    ),
    db: sqlite3.Connection = Depends(get_db),
):
    """Retrieve clicks filtered by a date and direction."""
    # Validate and parse the date
    try:
        date_obj = datetime.datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Expected YYYY-MM-DD.",
        )

    # Build ISO timestamp for midnight of the given date (UTC)
    date_iso = date_obj.replace(tzinfo=datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    # Map direction to SQL operator
    operator_map = {
        "<": "<",
        "<=": "<=",
        ">": ">",
        ">=": ">=",
    }
    sql_operator = operator_map[direction]

    query = f"""
        SELECT id, timestamp
        FROM clicks
        WHERE timestamp {sql_operator} ?
        ORDER BY timestamp ASC
        LIMIT ?
    """

    cursor = db.execute(query, (date_iso, MAX_ROWS_RETURNED))
    rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No clicks found for the given criteria.",
        )

    result = [ClickResponse(id=row["id"], timestamp=row["timestamp"]) for row in rows]
    return result


# ---------- Run the application ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)