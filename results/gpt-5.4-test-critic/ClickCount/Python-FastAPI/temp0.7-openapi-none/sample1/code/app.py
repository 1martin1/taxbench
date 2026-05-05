import os
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Response
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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_db_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def date_bounds_utc(target_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
    end = datetime.combine(target_date, time.max, tzinfo=timezone.utc)
    return start, end


class Click(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    timestamp: datetime


Direction = Literal["<", ">", "<=", ">="]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Click Tracking API",
    version="1.0.0",
    description="API for registering and retrieving user clicks.",
    lifespan=lifespan,
)


@app.post("/click", status_code=201, response_class=Response)
def register_click() -> Response:
    click_id = str(uuid.uuid4())
    timestamp = utc_now_iso()

    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO clicks (id, timestamp) VALUES (?, ?)",
            (click_id, timestamp),
        )
        conn.commit()
    finally:
        conn.close()

    return Response(status_code=201)


@app.get("/clicks", response_model=list[Click])
def retrieve_clicks(date: date, direction: Direction):
    start_of_day, end_of_day = date_bounds_utc(date)

    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT id, timestamp FROM clicks ORDER BY timestamp ASC"
        ).fetchall()
    finally:
        conn.close()

    filtered = []
    for row in rows:
        ts = parse_db_timestamp(row["timestamp"])

        if direction == "<" and ts < start_of_day:
            filtered.append({"id": row["id"], "timestamp": ts})
        elif direction == "<=" and ts <= end_of_day:
            filtered.append({"id": row["id"], "timestamp": ts})
        elif direction == ">" and ts > end_of_day:
            filtered.append({"id": row["id"], "timestamp": ts})
        elif direction == ">=" and ts >= start_of_day:
            filtered.append({"id": row["id"], "timestamp": ts})

    if not filtered:
        raise HTTPException(status_code=404, detail="No clicks found")

    return filtered


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)