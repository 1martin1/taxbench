import hashlib
import hmac
import os
import sqlite3
from contextlib import closing
from typing import Any

import uvicorn
from fastapi import Body, FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, Field


DB_PATH = "db.sqlite3"
MAX_CREDIT_CARD_LENGTH = 64
MAX_PHONE_LENGTH = 64
MAX_PHONE_NUMBERS = 100
MAX_TOTAL_PHONE_CHARS = 4096


def get_app_secret() -> str:
    secret = os.getenv("APP_SECRET")
    if not secret:
        raise RuntimeError("APP_SECRET environment variable is required")
    return secret


def hash_credit_card(credit_card: str) -> str:
    secret = get_app_secret().encode("utf-8")
    return hmac.new(secret, credit_card.encode("utf-8"), hashlib.sha256).hexdigest()


def normalize_string(value: Any) -> str:
    if not isinstance(value, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )
    return value.strip()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    try:
        with closing(get_connection()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS card_phone_associations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    credit_card_hash TEXT NOT NULL,
                    credit_card_masked TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    UNIQUE(credit_card_hash, phone)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_phone ON card_phone_associations(phone)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_credit_card_hash ON card_phone_associations(credit_card_hash)"
            )
            conn.commit()
    except sqlite3.Error as exc:
        raise RuntimeError("Failed to initialize database") from exc


class AssociateCardRequest(BaseModel):
    credit_card: str | None = Field(default=None, description="Number of the credit card")
    phone: str | None = Field(default=None, description="Phone number")


class RetrieveCardsRequest(BaseModel):
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


def verify_auth(authorization: str | None) -> None:
    expected = get_app_secret()
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[7:]
    if not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )


def mask_credit_card(credit_card: str) -> str:
    if len(credit_card) <= 4:
        return credit_card
    return "*" * (len(credit_card) - 4) + credit_card[-4:]


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post("/associate_card", status_code=status.HTTP_201_CREATED)
def associate_card(
    payload: AssociateCardRequest = Body(...),
    authorization: str | None = Header(default=None),
):
    verify_auth(authorization)

    if payload.credit_card is None or payload.phone is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    credit_card = normalize_string(payload.credit_card)
    phone = normalize_string(payload.phone)

    if not credit_card or not phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    if len(credit_card) > MAX_CREDIT_CARD_LENGTH or len(phone) > MAX_PHONE_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    credit_card_hash = hash_credit_card(credit_card)
    credit_card_masked = mask_credit_card(credit_card)

    try:
        with closing(get_connection()) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO card_phone_associations (credit_card_hash, credit_card_masked, phone)
                VALUES (?, ?, ?)
                """,
                (credit_card_hash, credit_card_masked, phone),
            )
            conn.commit()
    except sqlite3.Error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )

    return Response(status_code=status.HTTP_201_CREATED)


@app.post("/retrieve_cards", response_model=RetrieveCardsResponse, status_code=status.HTTP_200_OK)
def retrieve_cards(
    payload: RetrieveCardsRequest = Body(...),
    authorization: str | None = Header(default=None),
):
    verify_auth(authorization)

    if not isinstance(payload.phone_numbers, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    if not payload.phone_numbers or len(payload.phone_numbers) > MAX_PHONE_NUMBERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    phone_numbers = [normalize_string(phone) for phone in payload.phone_numbers]

    if any(not phone for phone in phone_numbers):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    if any(len(phone) > MAX_PHONE_LENGTH for phone in phone_numbers):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    if sum(len(phone) for phone in phone_numbers) > MAX_TOTAL_PHONE_CHARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    unique_phone_numbers = list(dict.fromkeys(phone_numbers))
    placeholders = ",".join("?" for _ in unique_phone_numbers)

    query = f"""
        SELECT credit_card_masked
        FROM card_phone_associations
        WHERE phone IN ({placeholders})
        GROUP BY credit_card_hash, credit_card_masked
        HAVING COUNT(DISTINCT phone) = ?
        ORDER BY credit_card_masked
    """

    try:
        with closing(get_connection()) as conn:
            rows = conn.execute(
                query,
                (*unique_phone_numbers, len(unique_phone_numbers)),
            ).fetchall()
    except sqlite3.Error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )

    card_numbers = [row["credit_card_masked"] for row in rows]

    if not card_numbers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    return RetrieveCardsResponse(card_numbers=card_numbers)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)