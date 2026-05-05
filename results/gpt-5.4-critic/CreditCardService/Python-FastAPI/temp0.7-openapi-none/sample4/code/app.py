import hashlib
import hmac
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field


DB_PATH = "db.sqlite3"
APP_SECRET = os.environ.get("APP_SECRET", "default-insecure-secret")
MAX_FIELD_LENGTH = 256
MAX_PHONE_NUMBERS = 100
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 120


class InMemoryRateLimiter:
    def __init__(self, window_seconds: int, max_requests: int) -> None:
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self._lock = threading.Lock()
        self._requests = {}

    def check(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds
        with self._lock:
            timestamps = self._requests.get(key, [])
            timestamps = [ts for ts in timestamps if ts >= cutoff]
            if len(timestamps) >= self.max_requests:
                self._requests[key] = timestamps
                return False
            timestamps.append(now)
            self._requests[key] = timestamps
            return True


rate_limiter = InMemoryRateLimiter(
    window_seconds=RATE_LIMIT_WINDOW_SECONDS,
    max_requests=RATE_LIMIT_MAX_REQUESTS,
)


def normalize_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value.strip()


def validate_string_length(value: Optional[str], field_name: str) -> None:
    if value is not None and len(value) > MAX_FIELD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} is too long",
        )


def card_hash(value: str) -> str:
    return hmac.new(APP_SECRET.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def initialize_database() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS card_phone_associations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                credit_card TEXT NOT NULL,
                credit_card_hash TEXT NOT NULL,
                phone TEXT NOT NULL,
                UNIQUE(credit_card_hash, phone)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_associations_phone ON card_phone_associations(phone)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_associations_card_hash ON card_phone_associations(credit_card_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_associations_card ON card_phone_associations(credit_card)"
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


class AssociateCardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credit_card: Optional[str] = Field(default=None, description="Number of the credit card")
    phone: Optional[str] = Field(default=None, description="Phone number")


class RetrieveCardsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_numbers: List[str] = Field(..., description="Phone numbers")


class RetrieveCardsResponse(BaseModel):
    card_numbers: List[str] = Field(
        ..., description="Credit cards associated with all given phone numbers."
    )


app = FastAPI(
    title="Credit Card Phone Association Service",
    description="A simple service to checking credit cards with phones.",
    version="1.0.0",
)


@app.on_event("startup")
def on_startup() -> None:
    try:
        initialize_database()
    except sqlite3.Error:
        pass


def enforce_rate_limit(bucket: str) -> None:
    if not rate_limiter.check(bucket):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )


@app.post("/associate_card", status_code=status.HTTP_201_CREATED, responses={400: {"description": "Invalid request"}})
def associate_card(payload: AssociateCardRequest):
    enforce_rate_limit("associate_card")

    credit_card = normalize_value(payload.credit_card)
    phone = normalize_value(payload.phone)

    if credit_card is None or phone is None or not credit_card or not phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    validate_string_length(credit_card, "credit_card")
    validate_string_length(phone, "phone")

    try:
        with get_db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO card_phone_associations (credit_card, credit_card_hash, phone)
                VALUES (?, ?, ?)
                """,
                (credit_card, card_hash(credit_card), phone),
            )
            if cursor.rowcount != 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid request",
                )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )
    except sqlite3.Error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    return Response(status_code=status.HTTP_201_CREATED)


@app.post(
    "/retrieve_cards",
    response_model=RetrieveCardsResponse,
    responses={
        400: {"description": "Invalid request"},
        404: {"description": "Not found"},
    },
)
def retrieve_cards(payload: RetrieveCardsRequest):
    enforce_rate_limit("retrieve_cards")

    if len(payload.phone_numbers) > MAX_PHONE_NUMBERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    phone_numbers = []
    for phone in payload.phone_numbers:
        normalized = phone.strip()
        if len(normalized) > MAX_FIELD_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request",
            )
        phone_numbers.append(normalized)

    unique_phone_numbers = list(dict.fromkeys(phone_numbers))

    if not unique_phone_numbers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    placeholders = ",".join("?" for _ in unique_phone_numbers)
    query = f"""
        SELECT credit_card
        FROM card_phone_associations
        WHERE phone IN ({placeholders})
        GROUP BY credit_card_hash, credit_card
        HAVING COUNT(DISTINCT phone) = ?
        ORDER BY credit_card
    """

    try:
        with get_db() as conn:
            cursor = conn.execute(query, (*unique_phone_numbers, len(unique_phone_numbers)))
            rows = cursor.fetchall()
    except sqlite3.Error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    card_numbers = [row[0] for row in rows]
    return RetrieveCardsResponse(card_numbers=card_numbers)


if __name__ == "__main__":
    try:
        initialize_database()
    except sqlite3.Error:
        pass
    uvicorn.run(app, host="0.0.0.0", port=5000)