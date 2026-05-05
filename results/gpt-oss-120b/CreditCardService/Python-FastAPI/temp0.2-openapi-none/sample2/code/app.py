import os
import sqlite3
import threading
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field, validator

# -------------------------------------------------
# Database setup
# -------------------------------------------------
DB_PATH = "db.sqlite3"
# Allow usage from multiple threads (FastAPI may use thread workers)
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
db_lock = threading.Lock()


def init_db() -> None:
    with db_lock, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS phones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS card_phone (
                card_id INTEGER NOT NULL,
                phone_id INTEGER NOT NULL,
                UNIQUE(card_id, phone_id),
                FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE CASCADE,
                FOREIGN KEY(phone_id) REFERENCES phones(id) ON DELETE CASCADE
            );
            """
        )


def get_or_create_card(card_number: str) -> int:
    with db_lock, conn:
        cur = conn.execute("SELECT id FROM cards WHERE number = ?", (card_number,))
        row = cur.fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO cards (number) VALUES (?)", (card_number,)
        )
        return cur.lastrowid


def get_or_create_phone(phone_number: str) -> int:
    with db_lock, conn:
        cur = conn.execute("SELECT id FROM phones WHERE number = ?", (phone_number,))
        row = cur.fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO phones (number) VALUES (?)", (phone_number,)
        )
        return cur.lastrowid


def associate(card_number: str, phone_number: str) -> None:
    card_id = get_or_create_card(card_number)
    phone_id = get_or_create_phone(phone_number)
    with db_lock, conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO card_phone (card_id, phone_id)
            VALUES (?, ?)
            """,
            (card_id, phone_id),
        )


def get_card_ids_for_phone(phone_number: str) -> set[int]:
    with db_lock, conn:
        cur = conn.execute(
            """
            SELECT cp.card_id
            FROM card_phone cp
            JOIN phones p ON cp.phone_id = p.id
            WHERE p.number = ?
            """,
            (phone_number,),
        )
        return {row["card_id"] for row in cur.fetchall()}


def get_card_numbers_by_ids(card_ids: set[int]) -> List[str]:
    if not card_ids:
        return []
    placeholders = ",".join("?" for _ in card_ids)
    query = f"SELECT number FROM cards WHERE id IN ({placeholders})"
    with db_lock, conn:
        cur = conn.execute(query, tuple(card_ids))
        return [row["number"] for row in cur.fetchall()]


# -------------------------------------------------
# FastAPI app and models
# -------------------------------------------------
app = FastAPI(title="Credit Card Phone Association Service", version="1.0.0")


class AssociateRequest(BaseModel):
    credit_card: str = Field(..., description="Number of the credit card")
    phone: str = Field(..., description="Phone number")

    @validator("credit_card", "phone")
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()


class RetrieveRequest(BaseModel):
    phone_numbers: List[str] = Field(..., description="Phone numbers")

    @validator("phone_numbers")
    def non_empty_list(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("list must contain at least one phone number")
        cleaned = [p.strip() for p in v if p.strip()]
        if not cleaned:
            raise ValueError("phone numbers cannot be empty strings")
        return cleaned


class RetrieveResponse(BaseModel):
    card_numbers: List[str] = Field(..., description="Credit cards associated with all given phone numbers.")


@app.on_event("startup")
def on_startup():
    init_db()
    # Load APP_SECRET if needed (not used in this simple example)
    _ = os.getenv("APP_SECRET")


@app.post("/associate_card", status_code=status.HTTP_201_CREATED, summary="Create a new association of a credit card number with a phone number.", operationId="associateCard")
def associate_card(request: AssociateRequest):
    try:
        associate(request.credit_card, request.phone)
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail="Database error")
    return {"detail": "Association created successfully"}


@app.post("/retrieve_cards", response_model=RetrieveResponse, summary="Retrieve cards associated with a set of phone numbers. A card must be associated with all given phone numbers to be returned.", operationId="retrieveCard")
def retrieve_cards(request: RetrieveRequest):
    # Get card id sets for each phone number
    card_id_sets = []
    for phone in request.phone_numbers:
        ids = get_card_ids_for_phone(phone)
        if not ids:
            # If any phone has no cards, intersection will be empty
            card_id_sets = []
            break
        card_id_sets.append(ids)

    if not card_id_sets:
        raise HTTPException(status_code=404, detail="No cards found for the provided phone numbers")

    # Intersection of all sets
    common_ids = set.intersection(*card_id_sets) if card_id_sets else set()
    if not common_ids:
        raise HTTPException(status_code=404, detail="No cards found for the provided phone numbers")

    card_numbers = get_card_numbers_by_ids(common_ids)
    return RetrieveResponse(card_numbers=card_numbers)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)