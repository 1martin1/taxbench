import os
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict


DB_PATH = "db.sqlite3"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db_connection()
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


def register_click() -> dict:
    click_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
            (click_id, timestamp),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": click_id, "timestamp": timestamp}


def get_clicks_by_date(filter_date: date, direction: str) -> list[dict]:
    start_of_day = datetime.combine(filter_date, time.min, tzinfo=timezone.utc)
    end_of_day = datetime.combine(filter_date, time.max, tzinfo=timezone.utc)

    if direction == "<":
        operator = "<"
        compare_value = start_of_day.isoformat()
    elif direction == "<=":
        operator = "<="
        compare_value = end_of_day.isoformat()
    elif direction == ">":
        operator = ">"
        compare_value = end_of_day.isoformat()
    elif direction == ">=":
        operator = ">="
        compare_value = start_of_day.isoformat()
    else:
        raise ValueError("Invalid direction")

    conn = get_db_connection()
    try:
        cursor = conn.execute(
            f"SELECT id, timestamp FROM clicks WHERE timestamp {operator} ? ORDER BY timestamp ASC",
            (compare_value,),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    return [{"id": row["id"], "timestamp": row["timestamp"]} for row in rows]


class Click(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "timestamp": "2022-01-01T12:00:00+00:00",
            }
        }
    )

    id: str
    timestamp: datetime


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks.",
    lifespan=lifespan,
)


@app.post("/click", status_code=201)
def create_click():
    register_click()
    return Response(status_code=201)


@app.get("/clicks", response_model=list[Click])
def read_clicks(
    date_value: date = Query(
        ...,
        alias="date",
        description="The date to filter clicks.",
        examples=["2022-01-01"],
    ),
    direction: Literal["<", ">", "<=", ">="] = Query(
        ...,
        description="Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date.",
    ),
):
    try:
        clicks = get_clicks_by_date(date_value, direction)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid request")

    if not clicks:
        raise HTTPException(status_code=404, detail="No clicks found")

    return clicks


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    uvicorn.run(app, host="0.0.0.0", port=port)