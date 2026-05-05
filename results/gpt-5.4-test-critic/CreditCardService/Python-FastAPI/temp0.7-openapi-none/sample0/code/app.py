import os
import re
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from typing import List

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


DB_PATH = "db.sqlite3"

CARD_PATTERN = re.compile(r"^\d{12,19}$")
PHONE_PATTERN = re.compile(r"^\+?[0-9()\-\s]{3,32}$")


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
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


def normalize_credit_card(value: str) -> str:
    normalized = value.strip().replace(" ", "").replace("-", "")
    if not normalized or not CARD_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="Invalid request")
    return normalized


def normalize_phone(value: str) -> str:
    normalized = value.strip()
    if not normalized or not PHONE_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="Invalid request")
    return normalized


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


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": "Invalid request"})


@app.post("/associate_card", status_code=201)
def associate_card(payload: AssociateCardRequest):
    credit_card = normalize_credit_card(payload.credit_card)
    phone = normalize_phone(payload.phone)

    with get_db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO associations (credit_card, phone)
            VALUES (?, ?)
            """,
            (credit_card, phone),
        )

    return {"message": "Association created successfully"}


@app.post("/retrieve_cards", response_model=RetrieveCardsResponse, status_code=200)
def retrieve_cards(payload: RetrieveCardsRequest):
    if not payload.phone_numbers:
        raise HTTPException(status_code=400, detail="Invalid request")

    phone_numbers = [normalize_phone(phone) for phone in payload.phone_numbers]
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
        raise HTTPException(status_code=404, detail="Not found")

    return RetrieveCardsResponse(card_numbers=card_numbers)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)