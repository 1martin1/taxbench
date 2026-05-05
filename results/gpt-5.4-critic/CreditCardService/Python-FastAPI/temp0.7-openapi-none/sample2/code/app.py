import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


DB_PATH = "db.sqlite3"

MAX_STRING_LENGTH = 256
MAX_PHONE_NUMBERS = 100

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_ASSOCIATE_MAX_REQUESTS = 120
RATE_LIMIT_RETRIEVE_MAX_REQUESTS = 60

_rate_limit_lock = threading.Lock()
_rate_limit_store: dict[tuple[str, str], list[float]] = {}


app = FastAPI(
    title="Credit Card Phone Association Service",
    description="A simple service to checking credit cards with phones.",
    version="1.0.0",
)


class AssociateCardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credit_card: str | None = Field(
        default=None, description="Number of the credit card"
    )
    phone: str | None = Field(default=None, description="Phone number")


class RetrieveCardsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_numbers: list[str] = Field(..., description="Phone numbers")


class RetrieveCardsResponse(BaseModel):
    card_numbers: list[str] = Field(
        ..., description="Credit cards associated with all given phone numbers."
    )


@contextmanager
def get_db():
    connection = sqlite3.connect(DB_PATH, timeout=5)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS associations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                credit_card TEXT NOT NULL,
                phone TEXT NOT NULL,
                UNIQUE(credit_card, phone)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_associations_phone ON associations(phone)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_associations_credit_card ON associations(credit_card)"
        )


def _client_identifier(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _check_rate_limit(request: Request, bucket: str, max_requests: int) -> None:
    now = time.time()
    key = (_client_identifier(request), bucket)

    with _rate_limit_lock:
        timestamps = _rate_limit_store.get(key, [])
        cutoff = now - RATE_LIMIT_WINDOW_SECONDS
        timestamps = [ts for ts in timestamps if ts > cutoff]

        if len(timestamps) >= max_requests:
            _rate_limit_store[key] = timestamps
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests",
            )

        timestamps.append(now)
        _rate_limit_store[key] = timestamps


def _normalize_required_string(value: Any, field_name: str) -> str:
    if value is None or not isinstance(value, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    normalized = value.strip()
    if not normalized or len(normalized) > MAX_STRING_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )
    return normalized


def _normalize_phone_numbers(values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    if len(values) > MAX_PHONE_NUMBERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    normalized_values: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request",
            )
        normalized = value.strip()
        if not normalized or len(normalized) > MAX_STRING_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request",
            )
        normalized_values.append(normalized)

    return normalized_values


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid request"},
    )


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.post(
    "/associate_card",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Association created successfully"},
        400: {"description": "Invalid request"},
        429: {"description": "Too many requests"},
        500: {"description": "Internal server error"},
    },
)
def associate_card(payload: AssociateCardRequest, request: Request):
    _check_rate_limit(
        request, "associate_card", RATE_LIMIT_ASSOCIATE_MAX_REQUESTS
    )

    credit_card = _normalize_required_string(payload.credit_card, "credit_card")
    phone = _normalize_required_string(payload.phone, "phone")

    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO associations (credit_card, phone)
                VALUES (?, ?)
                """,
                (credit_card, phone),
            )
    except sqlite3.Error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )

    return Response(status_code=status.HTTP_201_CREATED)


@app.post(
    "/retrieve_cards",
    response_model=RetrieveCardsResponse,
    responses={
        200: {"description": "Credit cards found"},
        400: {"description": "Invalid request"},
        404: {"description": "Not found"},
        429: {"description": "Too many requests"},
        500: {"description": "Internal server error"},
    },
)
def retrieve_cards(payload: RetrieveCardsRequest, request: Request):
    _check_rate_limit(
        request, "retrieve_cards", RATE_LIMIT_RETRIEVE_MAX_REQUESTS
    )

    phone_numbers = _normalize_phone_numbers(payload.phone_numbers)
    unique_phone_numbers = list(dict.fromkeys(phone_numbers))

    placeholders = ",".join("?" for _ in unique_phone_numbers)
    query = f"""
        SELECT credit_card
        FROM associations
        WHERE phone IN ({placeholders})
        GROUP BY credit_card
        HAVING COUNT(DISTINCT phone) = ?
        ORDER BY credit_card
    """

    try:
        with get_db() as conn:
            cursor = conn.execute(
                query, (*unique_phone_numbers, len(unique_phone_numbers))
            )
            rows = cursor.fetchall()
    except sqlite3.Error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )

    card_numbers = [row[0] for row in rows]

    if not card_numbers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    return RetrieveCardsResponse(card_numbers=card_numbers)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)