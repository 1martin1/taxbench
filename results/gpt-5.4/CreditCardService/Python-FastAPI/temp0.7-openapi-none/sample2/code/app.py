import os
import sqlite3
from contextlib import contextmanager
from typing import List

import uvicorn
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field, ConfigDict


DATABASE_PATH = "db.sqlite3"

app = FastAPI(
    title="Credit Card Phone Association Service",
    description="A simple service to checking credit cards with phones.",
    version="1.0.0",
)


def init_db() -> None:
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
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
            "CREATE INDEX IF NOT EXISTS idx_associations_credit_card ON associations(credit_card)"
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

    credit_card: str = Field(..., description="Number of the credit card")
    phone: str = Field(..., description="Phone number")


class RetrieveCardsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_numbers: List[str] = Field(..., description="Phone numbers", min_length=1)


class RetrieveCardsResponse(BaseModel):
    card_numbers: List[str] = Field(
        ..., description="Credit cards associated with all given phone numbers."
    )


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post("/associate_card", status_code=status.HTTP_201_CREATED)
def associate_card(payload: AssociateCardRequest):
    credit_card = payload.credit_card.strip()
    phone = payload.phone.strip()

    if not credit_card or not phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="credit_card and phone must be non-empty strings",
        )

    with get_db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO associations (credit_card, phone)
            VALUES (?, ?)
            """,
            (credit_card, phone),
        )

    return {"message": "Association created successfully"}


@app.post("/retrieve_cards", response_model=RetrieveCardsResponse)
def retrieve_cards(payload: RetrieveCardsRequest):
    phone_numbers = [phone.strip() for phone in payload.phone_numbers]

    if not phone_numbers or any(not phone for phone in phone_numbers):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="phone_numbers must contain at least one non-empty string",
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
        cursor = conn.execute(query, (*unique_phone_numbers, len(unique_phone_numbers)))
        rows = cursor.fetchall()

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