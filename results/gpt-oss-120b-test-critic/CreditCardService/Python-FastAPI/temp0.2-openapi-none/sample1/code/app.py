import os
import re
import sqlite3
from contextlib import contextmanager
from typing import Generator, List

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

app = FastAPI(
    title="Credit Card Phone Association Service",
    version="1.0.0",
    description="A simple service to checking credit cards with phones.",
)

DB_PATH = "db.sqlite3"


def init_db() -> None:
    """Create the associations table if it does not exist."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS associations (
                card_number TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                PRIMARY KEY (card_number, phone_number)
            )
            """
        )
        conn.commit()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """
    Provide a thread‑local SQLite connection.
    Each request gets its own connection, which is safe for concurrent use.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ---------- Exception handling ----------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Convert FastAPI's default 422 responses to 400 Bad Request
    as required by the OpenAPI specification.
    """
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid request"},
    )


# ---------- Validators ----------
def luhn_checksum(card_number: str) -> bool:
    """Return True if card_number passes the Luhn algorithm."""
    def digits_of(n):
        return [int(d) for d in n]

    digits = digits_of(card_number)
    odd_digits = digits[-1::-2]
    even_digits = digits[-2::-2]
    total = sum(odd_digits)
    for d in even_digits:
        total += sum(digits_of(str(d * 2)))
    return total % 10 == 0


PHONE_REGEX = re.compile(r"^\+?\d{7,15}$")


# ---------- Request / Response models ----------
class AssociateRequest(BaseModel):
    credit_card: str = Field(..., description="Number of the credit card")
    phone: str = Field(..., description="Phone number")

    @validator("credit_card")
    def validate_credit_card(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit():
            raise ValueError("Credit card must contain only digits")
        if not (13 <= len(v) <= 19):
            raise ValueError("Credit card length must be between 13 and 19 digits")
        if not luhn_checksum(v):
            raise ValueError("Credit card number failed Luhn check")
        return v

    @validator("phone")
    def validate_phone(cls, v: str) -> str:
        v = v.strip()
        if not PHONE_REGEX.fullmatch(v):
            raise ValueError("Phone number must be 7-15 digits, optionally prefixed with +")
        return v


class RetrieveRequest(BaseModel):
    phone_numbers: List[str] = Field(..., description="Phone numbers")

    @validator("phone_numbers")
    def validate_phone_numbers(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("phone_numbers must contain at least one phone number")
        cleaned = []
        for p in v:
            if not isinstance(p, str):
                raise ValueError("Each phone number must be a string")
            p = p.strip()
            if not PHONE_REGEX.fullmatch(p):
                raise ValueError(f"Invalid phone number format: {p}")
            cleaned.append(p)
        if not cleaned:
            raise ValueError("phone_numbers must contain non‑empty strings")
        return cleaned


class RetrieveResponse(BaseModel):
    card_numbers: List[str] = Field(..., description="Credit cards associated with all given phone numbers.")


# ---------- Endpoints ----------
@app.post(
    "/associate_card",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new association of a credit card number with a phone number.",
    responses={
        201: {"description": "Association created successfully"},
        400: {"description": "Invalid request"},
    },
)
def associate_card(request: AssociateRequest):
    with get_connection() as conn:
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO associations (card_number, phone_number)
                VALUES (?, ?)
                """,
                (request.credit_card, request.phone),
            )
            conn.commit()
        except sqlite3.Error as e:
            raise HTTPException(status_code=400, detail="Database error") from e
    return {"detail": "Association created successfully"}


@app.post(
    "/retrieve_cards",
    response_model=RetrieveResponse,
    summary="Retrieve cards associated with a set of phone numbers. A card must be associated with all given phone numbers to be returned.",
    responses={
        200: {"description": "Credit cards found"},
        400: {"description": "Invalid request"},
        404: {"description": "Not found"},
    },
)
def retrieve_cards(request: RetrieveRequest):
    phone_numbers = request.phone_numbers
    placeholders = ",".join("?" for _ in phone_numbers)

    sql = f"""
        SELECT card_number
        FROM associations
        WHERE phone_number IN ({placeholders})
        GROUP BY card_number
        HAVING COUNT(DISTINCT phone_number) = ?
    """

    with get_connection() as conn:
        try:
            cursor = conn.execute(sql, (*phone_numbers, len(phone_numbers)))
            rows = cursor.fetchall()
        except sqlite3.Error as e:
            raise HTTPException(status_code=400, detail="Database error") from e

    if not rows:
        raise HTTPException(
            status_code=404, detail="No credit cards found for the given phone numbers"
        )

    card_numbers = [row["card_number"] for row in rows]
    return RetrieveResponse(card_numbers=card_numbers)


# ---------- Run server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)