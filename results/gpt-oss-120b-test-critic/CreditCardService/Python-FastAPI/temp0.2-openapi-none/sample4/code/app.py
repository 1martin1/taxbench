import os
import re
import sqlite3
from typing import List, Set

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

app = FastAPI(title="Credit Card Phone Association Service", version="1.0.0")

DB_PATH = "db.sqlite3"


def get_db():
    """Dependency that provides a SQLite connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Create the associations table if it does not exist."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS associations (
                credit_card TEXT NOT NULL,
                phone TEXT NOT NULL,
                PRIMARY KEY (credit_card, phone)
            )
            """
        )
        conn.commit()


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    # Load APP_SECRET if needed (not used in this simple example)
    _ = os.getenv("APP_SECRET")


def luhn_checksum(card_number: str) -> bool:
    """Validate credit card number using Luhn algorithm."""
    total = 0
    num_digits = len(card_number)
    oddeven = num_digits & 1

    for count in range(num_digits):
        digit = int(card_number[count])

        if not ((count & 1) ^ oddeven):
            digit = digit * 2
            if digit > 9:
                digit -= 9

        total += digit

    return (total % 10) == 0


class AssociateRequest(BaseModel):
    credit_card: str = Field(..., description="Number of the credit card")
    phone: str = Field(..., description="Phone number")

    @validator("credit_card")
    def validate_credit_card(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit():
            raise ValueError("credit_card must contain only digits")
        if not 13 <= len(v) <= 19:
            raise ValueError("credit_card length must be between 13 and 19 digits")
        if not luhn_checksum(v):
            raise ValueError("credit_card failed Luhn checksum validation")
        return v

    @validator("phone")
    def validate_phone(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("phone must not be empty")
        # Simple phone validation: digits, optional leading '+', length 7-15
        if not re.fullmatch(r"\+?\d{7,15}", v):
            raise ValueError("phone must contain only digits and optional leading '+' (7-15 characters)")
        return v


class RetrieveRequest(BaseModel):
    phone_numbers: List[str] = Field(..., description="Phone numbers")

    @validator("phone_numbers")
    def validate_phone_numbers(cls, v: List[str]) -> List[str]:
        if not isinstance(v, list) or len(v) == 0:
            raise ValueError("phone_numbers must be a non‑empty list")
        cleaned: List[str] = []
        for phone in v:
            if not isinstance(phone, str):
                raise ValueError("each phone number must be a string")
            phone = phone.strip()
            if not phone:
                raise ValueError("phone numbers must not be empty strings")
            if not re.fullmatch(r"\+?\d{7,15}", phone):
                raise ValueError("phone numbers must contain only digits and optional leading '+' (7-15 characters)")
            cleaned.append(phone)
        return cleaned


@app.post(
    "/associate_card",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new association of a credit card number with a phone number.",
    operation_id="associateCard",
)
def associate_card(
    request: AssociateRequest, db: sqlite3.Connection = Depends(get_db)
) -> JSONResponse:
    try:
        db.execute(
            """
            INSERT OR IGNORE INTO associations (credit_card, phone)
            VALUES (?, ?)
            """,
            (request.credit_card, request.phone),
        )
        db.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Database error")
    return JSONResponse(
        status_code=status.HTTP_201_CREATED, content={"detail": "Association created"}
    )


@app.post(
    "/retrieve_cards",
    response_model=dict,
    summary="Retrieve cards associated with a set of phone numbers.",
    operation_id="retrieveCard",
)
def retrieve_cards(
    request: RetrieveRequest, db: sqlite3.Connection = Depends(get_db)
) -> dict:
    # Deduplicate phone numbers for the query logic
    distinct_phones: List[str] = list(dict.fromkeys(request.phone_numbers))
    placeholders = ",".join("?" for _ in distinct_phones)
    distinct_count = len(distinct_phones)

    query = f"""
        SELECT credit_card
        FROM associations
        WHERE phone IN ({placeholders})
        GROUP BY credit_card
        HAVING COUNT(DISTINCT phone) = ?
    """

    try:
        cursor = db.execute(query, (*distinct_phones, distinct_count))
        rows = cursor.fetchall()
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Database error")

    if not rows:
        raise HTTPException(
            status_code=404, detail="No credit cards found for the given phone numbers"
        )

    card_numbers = [row["credit_card"] for row in rows]
    return {"card_numbers": card_numbers}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)