import os
import re
import hmac
import hashlib
import sqlite3
from contextlib import contextmanager, asynccontextmanager
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict


DATABASE_PATH = "db.sqlite3"
APP_SECRET = os.getenv("APP_SECRET", "")

CARD_REGEX = re.compile(r"^\d{12,19}$")
PHONE_REGEX = re.compile(r"^\+?[0-9][0-9\-\s\(\)]{3,24}$")


def normalize_credit_card(value: str) -> str:
    normalized = re.sub(r"[\s\-]", "", value.strip())
    if not CARD_REGEX.fullmatch(normalized):
        raise ValueError("Invalid credit_card")
    return normalized


def normalize_phone(value: str) -> str:
    stripped = value.strip()
    if not PHONE_REGEX.fullmatch(stripped):
        raise ValueError("Invalid phone")
    normalized = re.sub(r"[\s\-\(\)]", "", stripped)
    if normalized.startswith("+"):
        digits = "+" + re.sub(r"\D", "", normalized[1:])
    else:
        digits = re.sub(r"\D", "", normalized)
    if digits.startswith("+"):
        digit_count = len(digits) - 1
    else:
        digit_count = len(digits)
    if digit_count < 4 or digit_count > 15:
        raise ValueError("Invalid phone")
    return digits


def card_hash(card_number: str) -> str:
    secret = APP_SECRET.encode("utf-8")
    return hmac.new(secret, card_number.encode("utf-8"), hashlib.sha256).hexdigest()


def init_db() -> None:
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS associations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                credit_card TEXT NOT NULL,
                credit_card_hash TEXT NOT NULL,
                phone TEXT NOT NULL,
                UNIQUE(credit_card, phone),
                UNIQUE(credit_card_hash, phone)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_associations_phone ON associations(phone)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_associations_credit_card ON associations(credit_card)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_associations_credit_card_hash ON associations(credit_card_hash)"
        )
        conn.commit()

        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(associations)").fetchall()
        }
        if "credit_card_hash" not in columns:
            conn.execute(
                "ALTER TABLE associations ADD COLUMN credit_card_hash TEXT"
            )
            conn.execute(
                "UPDATE associations SET credit_card_hash = '' WHERE credit_card_hash IS NULL"
            )
            conn.commit()

        rows = conn.execute(
            "SELECT id, credit_card FROM associations WHERE credit_card_hash IS NULL OR credit_card_hash = ''"
        ).fetchall()
        if rows:
            for row_id, credit_card_value in rows:
                conn.execute(
                    "UPDATE associations SET credit_card_hash = ? WHERE id = ?",
                    (card_hash(credit_card_value), row_id),
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
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid request"},
    )


def require_retrieve_authorization(authorization: Optional[str]) -> None:
    expected_secret = APP_SECRET.strip()
    if not expected_secret:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )
    provided = authorization[7:].strip()
    if not hmac.compare_digest(provided, expected_secret):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )


@app.post(
    "/associate_card",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid request"}},
)
def associate_card(payload: AssociateCardRequest):
    try:
        credit_card = normalize_credit_card(payload.credit_card)
        phone = normalize_phone(payload.phone)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    with get_db() as conn:
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO associations (credit_card, credit_card_hash, phone)
                VALUES (?, ?, ?)
                """,
                (credit_card, card_hash(credit_card), phone),
            )
        except sqlite3.DatabaseError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request",
            )

    return {}


@app.post(
    "/retrieve_cards",
    response_model=RetrieveCardsResponse,
    responses={
        400: {"description": "Invalid request"},
        404: {"description": "Not found"},
    },
)
def retrieve_cards(
    payload: RetrieveCardsRequest,
    authorization: Optional[str] = Header(default=None),
):
    require_retrieve_authorization(authorization)

    try:
        phone_numbers = [normalize_phone(phone) for phone in payload.phone_numbers]
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    if not phone_numbers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

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

    with get_db() as conn:
        try:
            cursor = conn.execute(
                query, (*unique_phone_numbers, len(unique_phone_numbers))
            )
            rows = cursor.fetchall()
        except sqlite3.DatabaseError:
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