import os
import sqlite3
from typing import List

from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
from starlette.requests import Request

app = FastAPI(title="Credit Card Phone Association Service", version="1.0.0")

DB_PATH = "db.sqlite3"


def get_db():
    """Dependency that provides a DB connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
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
def on_startup():
    # Ensure DB and table exist
    init_db()
    # Load APP_SECRET if needed (not used in this simple example)
    _ = os.getenv("APP_SECRET")


class AssociateRequest(BaseModel):
    credit_card: str = Field(..., description="Number of the credit card")
    phone: str = Field(..., description="Phone number")

    @validator("credit_card", "phone")
    def not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()


class RetrieveRequest(BaseModel):
    phone_numbers: List[str] = Field(..., description="Phone numbers")

    @validator("phone_numbers")
    def non_empty_list(cls, v):
        if not isinstance(v, list) or len(v) == 0:
            raise ValueError("phone_numbers must be a non‑empty list")
        cleaned = [p.strip() for p in v if p and p.strip()]
        if len(cleaned) != len(v):
            raise ValueError("phone numbers must not be empty strings")
        return cleaned


@app.post(
    "/associate_card",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new association of a credit card number with a phone number.",
    operation_id="associateCard",
)
def associate_card(request: AssociateRequest, db: sqlite3.Connection = Depends(get_db)):
    try:
        db.execute(
            """
            INSERT OR IGNORE INTO associations (credit_card, phone)
            VALUES (?, ?)
            """,
            (request.credit_card, request.phone),
        )
        db.commit()
    except sqlite3.Error as e:
        raise HTTPException(status_code=400, detail="Database error")
    return JSONResponse(status_code=status.HTTP_201_CREATED, content={"detail": "Association created"})


@app.post(
    "/retrieve_cards",
    response_model=dict,
    summary="Retrieve cards associated with a set of phone numbers.",
    operation_id="retrieveCard",
)
def retrieve_cards(request: RetrieveRequest, db: sqlite3.Connection = Depends(get_db)):
    phone_list = request.phone_numbers
    placeholders = ",".join("?" for _ in phone_list)
    query = f"""
        SELECT credit_card
        FROM associations
        WHERE phone IN ({placeholders})
        GROUP BY credit_card
        HAVING COUNT(DISTINCT phone) = ?
    """
    try:
        cursor = db.execute(query, (*phone_list, len(phone_list)))
        rows = cursor.fetchall()
    except sqlite3.Error as e:
        raise HTTPException(status_code=400, detail="Database error")

    if not rows:
        raise HTTPException(status_code=404, detail="No credit cards found for the given phone numbers")

    card_numbers = [row["credit_card"] for row in rows]
    return {"card_numbers": card_numbers}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)