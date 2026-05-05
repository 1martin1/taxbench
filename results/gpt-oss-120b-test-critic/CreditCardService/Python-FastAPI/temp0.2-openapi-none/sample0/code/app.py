import os
import sqlite3
from typing import List

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

app = FastAPI(
    title="Credit Card Phone Association Service",
    version="1.0.0",
    description="A simple service to checking credit cards with phones.",
)

DB_PATH = "db.sqlite3"


def get_db_connection() -> sqlite3.Connection:
    """Create a new SQLite connection with row factory for dict‑like access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize the SQLite database with the required table."""
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS association (
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


class AssociateCardRequest(BaseModel):
    credit_card: str = Field(..., description="Number of the credit card")
    phone: str = Field(..., description="Phone number")

    @field_validator("credit_card", "phone")
    @classmethod
    def not_empty(cls, v: str) -> str:
        """Strip whitespace and ensure the field is not empty."""
        if not isinstance(v, str):
            raise ValueError("must be a string")
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned


class RetrieveCardsRequest(BaseModel):
    phone_numbers: List[str] = Field(..., description="Phone numbers")

    @field_validator("phone_numbers")
    @classmethod
    def clean_and_validate(cls, v: List[str]) -> List[str]:
        """Remove empty strings, strip whitespace, and ensure at least one phone number."""
        if not isinstance(v, list):
            raise ValueError("phone_numbers must be a list")
        cleaned = [p.strip() for p in v if isinstance(p, str) and p.strip()]
        if not cleaned:
            raise ValueError("phone_numbers must contain at least one non‑empty string")
        return cleaned


class RetrieveCardsResponse(BaseModel):
    card_numbers: List[str] = Field(..., description="Credit cards associated with all given phone numbers.")


@app.post(
    "/associate_card",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new association of a credit card number with a phone number.",
    operation_id="associateCard",
)
def associate_card(request: AssociateCardRequest):
    """
    Store a credit‑card / phone association.
    Duplicate associations are ignored (idempotent).
    """
    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO association (credit_card, phone) VALUES (?, ?)",
                (request.credit_card, request.phone),
            )
            conn.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Database error")
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={"detail": "Association created successfully"},
    )


@app.post(
    "/retrieve_cards",
    response_model=RetrieveCardsResponse,
    summary="Retrieve cards associated with a set of phone numbers.",
    operation_id="retrieveCard",
)
def retrieve_cards(request: RetrieveCardsRequest):
    """
    Return credit cards that are associated with **all** supplied phone numbers.
    """
    phone_list = request.phone_numbers
    placeholders = ",".join("?" for _ in phone_list)
    query = f"""
        SELECT credit_card
        FROM association
        WHERE phone IN ({placeholders})
        GROUP BY credit_card
        HAVING COUNT(DISTINCT phone) = ?
    """
    params = phone_list + [len(phone_list)]

    try:
        with get_db_connection() as conn:
            cur = conn.execute(query, params)
            rows = cur.fetchall()
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Database error")

    if not rows:
        raise HTTPException(status_code=404, detail="No credit cards found for the given phone numbers")

    card_numbers = [row["credit_card"] for row in rows]
    return RetrieveCardsResponse(card_numbers=card_numbers)


if __name__ == "__main__":
    import uvicorn

    # Load optional secret (not used directly in this example)
    _ = os.getenv("APP_SECRET", "")

    uvicorn.run(app, host="0.0.0.0", port=5000)