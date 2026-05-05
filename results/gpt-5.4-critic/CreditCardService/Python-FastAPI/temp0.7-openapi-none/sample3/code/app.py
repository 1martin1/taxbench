import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from typing import List

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field, field_validator


DB_PATH = "db.sqlite3"
MAX_CREDIT_CARD_LENGTH = 19
MIN_CREDIT_CARD_LENGTH = 12
MAX_PHONE_LENGTH = 32
MAX_PHONE_NUMBERS = 200
APP_SECRET = os.getenv("APP_SECRET")

CARD_RE = re.compile(r"^\d{12,19}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9()\-\s]{0,30}$")


def _require_auth(x_app_secret: str | None) -> None:
    if not APP_SECRET:
        raise HTTPException(status_code=500, detail="Server misconfiguration")
    if x_app_secret is None or not secrets.compare_digest(x_app_secret, APP_SECRET):
        raise HTTPException(status_code=401, detail="Unauthorized")


def normalize_credit_card(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid request")
    normalized = "".join(value.split())
    if not normalized:
        raise ValueError("Invalid request")
    if len(normalized) < MIN_CREDIT_CARD_LENGTH or len(normalized) > MAX_CREDIT_CARD_LENGTH:
        raise ValueError("Invalid request")
    if not CARD_RE.fullmatch(normalized):
        raise ValueError("Invalid request")
    return normalized


def normalize_phone(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid request")
    normalized = value.strip()
    if not normalized:
        raise ValueError("Invalid request")
    if len(normalized) > MAX_PHONE_LENGTH:
        raise ValueError("Invalid request")
    if not PHONE_RE.fullmatch(normalized):
        raise ValueError("Invalid request")
    return normalized


def card_hash(card_number: str) -> str:
    if not APP_SECRET:
        raise RuntimeError("APP_SECRET is required")
    return hmac.new(APP_SECRET.encode("utf-8"), card_number.encode("utf-8"), hashlib.sha256).hexdigest()


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS associations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                credit_card_hash TEXT NOT NULL,
                credit_card_last4 TEXT NOT NULL,
                phone TEXT NOT NULL,
                UNIQUE(credit_card_hash, phone)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_associations_phone ON associations(phone)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_associations_card_hash ON associations(credit_card_hash)"
        )
        conn.commit()

        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(associations)").fetchall()
        }
        if "credit_card" in columns and "credit_card_hash" not in columns:
            rows = conn.execute(
                "SELECT id, credit_card, phone FROM associations"
            ).fetchall()
            conn.execute("ALTER TABLE associations ADD COLUMN credit_card_hash TEXT")
            conn.execute(
                "ALTER TABLE associations ADD COLUMN credit_card_last4 TEXT DEFAULT ''"
            )
            for row_id, plaintext_card, phone in rows:
                normalized = normalize_credit_card(str(plaintext_card))
                conn.execute(
                    """
                    UPDATE associations
                    SET credit_card_hash = ?, credit_card_last4 = ?
                    WHERE id = ?
                    """,
                    (card_hash(normalized), normalized[-4:], row_id),
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
    credit_card: str = Field(..., description="Number of the credit card", min_length=1, max_length=64)
    phone: str = Field(..., description="Phone number", min_length=1, max_length=MAX_PHONE_LENGTH)

    @field_validator("credit_card")
    @classmethod
    def validate_credit_card(cls, value: str) -> str:
        return normalize_credit_card(value)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return normalize_phone(value)

    class Config:
        extra = "forbid"


class RetrieveCardsRequest(BaseModel):
    phone_numbers: List[str] = Field(
        ...,
        description="Phone numbers",
        min_length=1,
        max_length=MAX_PHONE_NUMBERS,
    )

    @field_validator("phone_numbers")
    @classmethod
    def validate_phone_numbers(cls, values: List[str]) -> List[str]:
        normalized = [normalize_phone(v) for v in values]
        if not normalized:
            raise ValueError("Invalid request")
        return normalized

    class Config:
        extra = "forbid"


class RetrieveCardsResponse(BaseModel):
    card_numbers: List[str] = Field(
        ..., description="Credit cards associated with all given phone numbers."
    )

    class Config:
        extra = "forbid"


app = FastAPI(
    title="Credit Card Phone Association Service",
    description="A simple service to checking credit cards with phones.",
    version="1.0.0",
)


@app.on_event("startup")
def startup_event() -> None:
    if not APP_SECRET:
        raise RuntimeError("APP_SECRET environment variable is required")
    init_db()


@app.exception_handler(Exception)
async def unhandled_exception_handler(_, exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/associate_card", status_code=201, response_class=Response)
def associate_card(payload: AssociateCardRequest, x_app_secret: str | None = Header(default=None)):
    _require_auth(x_app_secret)

    credit_card = payload.credit_card
    phone = payload.phone
    hashed_card = card_hash(credit_card)
    last4 = credit_card[-4:]

    with get_db() as conn:
        try:
            existing = conn.execute(
                """
                SELECT 1 FROM associations
                WHERE credit_card_hash = ? AND phone = ?
                LIMIT 1
                """,
                (hashed_card, phone),
            ).fetchone()
            if existing:
                raise HTTPException(status_code=400, detail="Invalid request")

            conn.execute(
                """
                INSERT INTO associations (credit_card_hash, credit_card_last4, phone)
                VALUES (?, ?, ?)
                """,
                (hashed_card, last4, phone),
            )
        except HTTPException:
            raise
        except sqlite3.Error:
            raise HTTPException(status_code=400, detail="Invalid request")

    return Response(status_code=201)


@app.post("/retrieve_cards", response_model=RetrieveCardsResponse, status_code=200)
def retrieve_cards(payload: RetrieveCardsRequest, x_app_secret: str | None = Header(default=None)):
    _require_auth(x_app_secret)

    unique_phone_numbers = list(dict.fromkeys(payload.phone_numbers))
    if not unique_phone_numbers or len(unique_phone_numbers) > MAX_PHONE_NUMBERS:
        raise HTTPException(status_code=400, detail="Invalid request")

    placeholders = ",".join("?" for _ in unique_phone_numbers)
    query = f"""
        SELECT credit_card_last4
        FROM associations
        WHERE phone IN ({placeholders})
        GROUP BY credit_card_hash, credit_card_last4
        HAVING COUNT(DISTINCT phone) = ?
        ORDER BY credit_card_last4
    """

    with get_db() as conn:
        try:
            cursor = conn.execute(query, (*unique_phone_numbers, len(unique_phone_numbers)))
            rows = cursor.fetchall()
        except sqlite3.Error:
            raise HTTPException(status_code=400, detail="Invalid request")

    if not rows:
        raise HTTPException(status_code=404, detail="Not found")

    return RetrieveCardsResponse(card_numbers=[row[0] for row in rows])


if __name__ == "__main__":
    if not APP_SECRET:
        raise RuntimeError("APP_SECRET environment variable is required")
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)