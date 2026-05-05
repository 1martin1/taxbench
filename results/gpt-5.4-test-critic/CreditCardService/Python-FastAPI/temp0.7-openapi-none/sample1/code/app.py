import hashlib
import hmac
import os
import re
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from typing import Any, List

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

DATABASE_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "")

CARD_DIGITS_RE = re.compile(r"^\d{12,19}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9\-\s().]{2,31}$")


def normalize_credit_card(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid request")
    normalized = re.sub(r"[\s-]", "", value.strip())
    if not CARD_DIGITS_RE.fullmatch(normalized):
        raise ValueError("Invalid request")
    return normalized


def normalize_phone(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid request")
    normalized = value.strip()
    if not PHONE_RE.fullmatch(normalized):
        raise ValueError("Invalid request")
    digits = re.sub(r"\D", "", normalized)
    if len(digits) < 3 or len(digits) > 15:
        raise ValueError("Invalid request")
    if normalized.startswith("+"):
        return "+" + digits
    return digits


def card_fingerprint(card_number: str) -> str:
    key = APP_SECRET.encode("utf-8")
    msg = card_number.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def init_db() -> None:
    conn = sqlite3.connect(DATABASE_PATH)
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
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


class AssociateCardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credit_card: str = Field(..., description="Number of the credit card")
    phone: str = Field(..., description="Phone number")


class RetrieveCardsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_numbers: List[str] = Field(..., description="Phone numbers")


class RetrieveCardsResponse(BaseModel):
    card_numbers: List[str] = Field(
        ..., description="Credit cards associated with all given phone numbers."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Credit Card Phone Association Service",
    description="A simple service to checking credit cards with phones.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid request"},
    )


@app.exception_handler(ValidationError)
async def validation_exception_handler(
    request: Request, exc: ValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid request"},
    )


def parse_associate_card_request(data: Any) -> AssociateCardRequest:
    try:
        payload = AssociateCardRequest.model_validate(data)
        payload.credit_card = normalize_credit_card(payload.credit_card)
        payload.phone = normalize_phone(payload.phone)
        return payload
    except (ValidationError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )


def parse_retrieve_cards_request(data: Any) -> RetrieveCardsRequest:
    try:
        payload = RetrieveCardsRequest.model_validate(data)
        if not isinstance(payload.phone_numbers, list) or len(payload.phone_numbers) == 0:
            raise ValueError("Invalid request")
        payload.phone_numbers = [normalize_phone(phone) for phone in payload.phone_numbers]
        return payload
    except (ValidationError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )


@app.post("/associate_card", status_code=status.HTTP_201_CREATED)
async def associate_card(request: Request):
    payload = parse_associate_card_request(await request.json())
    stored_card = card_fingerprint(payload.credit_card)

    with get_db() as conn:
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO associations (credit_card, phone)
                VALUES (?, ?)
                """,
                (stored_card, payload.phone),
            )
        except (sqlite3.DatabaseError, sqlite3.Error):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request",
            )

    return {}


@app.post(
    "/retrieve_cards",
    response_model=RetrieveCardsResponse,
    status_code=status.HTTP_200_OK,
)
async def retrieve_cards(request: Request):
    payload = parse_retrieve_cards_request(await request.json())
    unique_phone_numbers = list(dict.fromkeys(payload.phone_numbers))
    placeholders = ",".join("?" for _ in unique_phone_numbers)

    query = f"""
        SELECT credit_card
        FROM associations
        WHERE phone IN ({placeholders})
        GROUP BY credit_card
        HAVING COUNT(DISTINCT phone) = ?
        ORDER BY credit_card
    """

    with get_db() as conn:
        try:
            cursor = conn.execute(
                query, (*unique_phone_numbers, len(unique_phone_numbers))
            )
            rows = cursor.fetchall()
        except (sqlite3.DatabaseError, sqlite3.Error):
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