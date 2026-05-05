import re
import sqlite3
from contextlib import contextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


DB_PATH = "db.sqlite3"

CARD_MIN_LENGTH = 12
CARD_MAX_LENGTH = 19
PHONE_MIN_LENGTH = 1
PHONE_MAX_LENGTH = 32

CARD_PATTERN = re.compile(r"^\d{12,19}$")
PHONE_PATTERN = re.compile(r"^\+?[0-9()\-\s]{1,32}$")


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
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
            "CREATE INDEX IF NOT EXISTS idx_associations_card ON associations(credit_card)"
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


def luhn_check(card_number: str) -> bool:
    total = 0
    reverse_digits = card_number[::-1]
    for index, char in enumerate(reverse_digits):
        digit = ord(char) - ord("0")
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def normalize_credit_card(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().replace(" ", "").replace("-", "")
    if not cleaned:
        return None
    if not CARD_PATTERN.fullmatch(cleaned):
        return None
    if not luhn_check(cleaned):
        return None
    return cleaned


def normalize_phone(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) < PHONE_MIN_LENGTH or len(cleaned) > PHONE_MAX_LENGTH:
        return None
    if not PHONE_PATTERN.fullmatch(cleaned):
        return None
    return cleaned


class AssociateCardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credit_card: str | None = Field(default=None, description="Number of the credit card")
    phone: str | None = Field(default=None, description="Phone number")


class RetrieveCardsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_numbers: list[str] = Field(..., description="Phone numbers")


class RetrieveCardsResponse(BaseModel):
    card_numbers: list[str] = Field(
        ..., description="Credit cards associated with all given phone numbers."
    )


app = FastAPI(
    title="Credit Card Phone Association Service",
    description="A simple service to checking credit cards with phones.",
    version="1.0.0",
)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid request"},
    )


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post("/associate_card", status_code=status.HTTP_201_CREATED)
def associate_card(payload: AssociateCardRequest):
    credit_card = normalize_credit_card(payload.credit_card)
    phone = normalize_phone(payload.phone)

    if credit_card is None or phone is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    with get_db() as conn:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO associations (credit_card, phone) VALUES (?, ?)",
                (credit_card, phone),
            )
        except sqlite3.Error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request",
            )

    return {"message": "Association created successfully"}


@app.post("/retrieve_cards", response_model=RetrieveCardsResponse)
def retrieve_cards(payload: RetrieveCardsRequest):
    if not isinstance(payload.phone_numbers, list) or not payload.phone_numbers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    normalized_phone_numbers: list[str] = []
    for phone in payload.phone_numbers:
        normalized = normalize_phone(phone)
        if normalized is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request",
            )
        normalized_phone_numbers.append(normalized)

    unique_phone_numbers = list(dict.fromkeys(normalized_phone_numbers))
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
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    card_numbers = [row[0] for row in rows]

    if not card_numbers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    return RetrieveCardsResponse(card_numbers=card_numbers)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)