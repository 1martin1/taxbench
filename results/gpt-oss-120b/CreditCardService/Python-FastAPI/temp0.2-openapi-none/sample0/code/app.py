import os
import sqlite3
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

app = FastAPI(title="Credit Card Phone Association Service", version="1.0.0")

DB_PATH = "db.sqlite3"


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
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
def on_startup():
    init_db()


class AssociateCardRequest(BaseModel):
    credit_card: str = Field(..., description="Number of the credit card")
    phone: str = Field(..., description="Phone number")

    @validator("credit_card", "phone")
    def not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()


class RetrieveCardsRequest(BaseModel):
    phone_numbers: List[str] = Field(..., description="Phone numbers")

    @validator("phone_numbers")
    def not_empty_list(cls, v):
        if not v:
            raise ValueError("phone_numbers must contain at least one phone number")
        cleaned = [p.strip() for p in v if p and p.strip()]
        if not cleaned:
            raise ValueError("phone_numbers must contain non‑empty strings")
        return cleaned


class RetrieveCardsResponse(BaseModel):
    card_numbers: List[str] = Field(..., description="Credit cards associated with all given phone numbers.")


@app.post("/associate_card", status_code=status.HTTP_201_CREATED, summary="Create a new association of a credit card number with a phone number.", operationId="associateCard")
def associate_card(request: AssociateCardRequest):
    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO association (credit_card, phone) VALUES (?, ?)",
                (request.credit_card, request.phone),
            )
            conn.commit()
    except sqlite3.Error as e:
        raise HTTPException(status_code=400, detail="Database error")
    return JSONResponse(status_code=status.HTTP_201_CREATED, content={"detail": "Association created successfully"})


@app.post("/retrieve_cards", response_model=RetrieveCardsResponse, summary="Retrieve cards associated with a set of phone numbers.", operationId="retrieveCard")
def retrieve_cards(request: RetrieveCardsRequest):
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
    except sqlite3.Error as e:
        raise HTTPException(status_code=400, detail="Database error")

    if not rows:
        raise HTTPException(status_code=404, detail="No credit cards found for the given phone numbers")

    card_numbers = [row["credit_card"] for row in rows]
    return RetrieveCardsResponse(card_numbers=card_numbers)


if __name__ == "__main__":
    import uvicorn

    # Optional: load secret (not used in this simple example)
    APP_SECRET = os.getenv("APP_SECRET", "")

    uvicorn.run(app, host="0.0.0.0", port=5000)