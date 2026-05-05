import os
import uuid
import sqlite3
from datetime import datetime, date, time
from typing import List

from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel, Field
import uvicorn

DATABASE = "db.sqlite3"

app = FastAPI(title="Click Tracking API", version="1.0.0", description="API for registering and retrieving user clicks.")


def get_connection():
    conn = sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES)
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


@app.on_event("startup")
def on_startup():
    init_db()


class ClickResponse(BaseModel):
    id: str = Field(..., description="The unique identifier for the click.")
    timestamp: datetime = Field(..., description="The timestamp of the click.")


@app.post("/click", status_code=201, summary="Register a click", description="Endpoint to register a user's click in the database.")
def register_click():
    click_id = str(uuid.uuid4())
    now = datetime.utcnow().replace(microsecond=0)  # ISO format without microseconds
    timestamp_str = now.isoformat() + "Z"  # explicit UTC marker

    with get_connection() as conn:
        try:
            conn.execute(
                "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
                (click_id, timestamp_str),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="Could not register click.")

    return {"id": click_id, "timestamp": timestamp_str}


@app.get(
    "/clicks",
    response_model=List[ClickResponse],
    summary="Retrieve clicks",
    description="Endpoint to retrieve clicks before or after a certain date.",
)
def retrieve_clicks(
    date_str: str = Query(..., description="The date to filter clicks.", example="2022-01-01"),
    direction: str = Query(
        ...,
        description="Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date.",
        regex="^(<|<=|>|>=)$",
    ),
):
    # Validate and parse date
    try:
        filter_date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    # Build SQL condition based on direction
    if direction == "<":
        sql_cond = "timestamp < ?"
        param = filter_date.isoformat()
    elif direction == "<=":
        sql_cond = "timestamp <= ?"
        param = filter_date.isoformat()
    elif direction == ">":
        sql_cond = "timestamp > ?"
        param = filter_date.isoformat()
    elif direction == ">=":
        sql_cond = "timestamp >= ?"
        param = filter_date.isoformat()
    else:
        raise HTTPException(status_code=400, detail="Invalid direction parameter.")

    query = f"SELECT id, timestamp FROM clicks WHERE {sql_cond} ORDER BY timestamp ASC"

    with get_connection() as conn:
        rows = conn.execute(query, (param,)).fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No clicks found.")

    result = [
        ClickResponse(id=row["id"], timestamp=datetime.fromisoformat(row["timestamp"].replace("Z", "")))
        for row in rows
    ]
    return result


if __name__ == "__main__":
    # Use host 0.0.0.0 and port 5000 as required
    uvicorn.run(app, host="0.0.0.0", port=5000)