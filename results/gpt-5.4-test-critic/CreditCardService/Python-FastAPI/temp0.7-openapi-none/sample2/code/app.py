import hashlib
import hmac
import os
import re
import sqlite3
from contextlib import contextmanager, asynccontextmanager
from typing import Any, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

DATABASE_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "default-insecure-secret-change-me")


def _normalize_credit_card(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("credit_card must be a string")
    normalized = re.sub(r"[\s-]+", "", value.strip())
    if not normalized:
        raise ValueError("credit_card must be a non-empty string")
    if not normalized.isdigit():
        raise ValueError("credit_card must contain only digits, spaces, or hyphens")
    if not 12 <= len(normalized) <= 19:
        raise ValueError("credit_card length must be between 12 and 19 digits")
    return normalized


def _normalize_phone(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("phone must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("phone must be a non-empty string")
    if not re.fullmatch(r"\+?[0-9][0-9()\-\s]{5,24}", normalized):
        raise ValueError("phone format is invalid")
    digits_only = re.sub(r"\D", "", normalized)
    if not 6 <= len(digits_only) <= 15:
        raise ValueError("phone must contain between 6 and 15 digits")
    return normalized


def _card_fingerprint(card_number: str) -> str:
    return hmac.new(
        APP_SECRET.encode("utf-8"),
        card_number.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _mask_card_number(card_number: str) -> str:
    if len(card_number) <= 4:
        return card_number
    return "*" * (len(card_number) - 4) + card_number[-4:]


def init_db() -> None:
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS associations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                credit_card TEXT NOT NULL,
                credit_card_fingerprint TEXT NOT NULL,
                phone TEXT NOT NULL,
                UNIQUE(credit_card_fingerprint, phone)
            )
            """
        )
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(associations)").fetchall()
        }
        if "credit_card_fingerprint" not in columns:
            conn.execute(
                "ALTER TABLE associations ADD COLUMN credit_card_fingerprint TEXT"
            )
            rows = conn.execute(
                "SELECT id, credit_card FROM associations WHERE credit_card_fingerprint IS NULL OR credit_card_fingerprint = ''"
            ).fetchall()
            for row_id, credit_card in rows:
                conn.execute(
                    "UPDATE associations SET credit_card_fingerprint = ? WHERE id = ?",
                    (_card_fingerprint(credit_card), row_id),
                )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_associations_phone ON associations(phone)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_associations_credit_card ON associations(credit_card)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_associations_credit_card_fingerprint ON associations(credit_card_fingerprint)"
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class AssociateCardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credit_card: Optional[str] = Field(None, description="Number of the credit card")
    phone: Optional[str] = Field(None, description="Phone number")

    @field_validator("credit_card")
    @classmethod
    def validate_credit_card(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _normalize_credit_card(value)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _normalize_phone(value)


class RetrieveCardsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_numbers: List[str] = Field(..., description="Phone numbers", min_length=1)

    @field_validator("phone_numbers")
    @classmethod
    def validate_phone_numbers(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("phone_numbers must contain at least one phone number")
        return [_normalize_phone(item) for item in value]


class RetrieveCardsResponse(BaseModel):
    card_numbers: List[str] = Field(
        ..., description="Credit cards associated with all given phone numbers."
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Credit Card Phone Association Service",
    description="A simple service to checking credit cards with phones.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": exc.errors()},
    )


@app.post("/associate_card", status_code=status.HTTP_201_CREATED)
def associate_card(payload: AssociateCardRequest):
    if payload.credit_card is None or payload.phone is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="credit_card and phone are required",
        )

    credit_card = payload.credit_card
    phone = payload.phone
    fingerprint = _card_fingerprint(credit_card)

    with get_db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO associations (credit_card, credit_card_fingerprint, phone)
            VALUES (?, ?, ?)
            """,
            (credit_card, fingerprint, phone),
        )

    return {"message": "Association created successfully"}


@app.post("/retrieve_cards", response_model=RetrieveCardsResponse)
def retrieve_cards(payload: RetrieveCardsRequest):
    phone_numbers = payload.phone_numbers

    unique_phone_numbers = list(dict.fromkeys(phone_numbers))
    placeholders = ",".join("?" for _ in unique_phone_numbers)

    query = f"""
        SELECT credit_card
        FROM associations
        WHERE phone IN ({placeholders})
        GROUP BY credit_card_fingerprint
        HAVING COUNT(DISTINCT phone) = ?
        ORDER BY credit_card
    """

    with get_db() as conn:
        cursor = conn.execute(query, (*unique_phone_numbers, len(unique_phone_numbers)))
        rows = cursor.fetchall()

    card_numbers = [_mask_card_number(row[0]) for row in rows]

    if not card_numbers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    return RetrieveCardsResponse(card_numbers=card_numbers)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)