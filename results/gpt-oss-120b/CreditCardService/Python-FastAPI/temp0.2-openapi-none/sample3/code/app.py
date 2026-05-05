import os
import sqlite3
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

app = FastAPI(title="Credit Card Phone Association Service", version="1.0.0")

DB_PATH = "db.sqlite3"

# ---------- Database Setup ----------
def get_db_connection() -> sqlite3.Connection:
    # Using check_same_thread=False to allow usage across FastAPI's async context
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # Table for cards
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT NOT NULL UNIQUE
        )
    """)
    # Table for phones
    cur.execute("""
        CREATE TABLE IF NOT EXISTS phones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT NOT NULL UNIQUE
        )
    """)
    # Association table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS card_phone (
            card_id INTEGER NOT NULL,
            phone_id INTEGER NOT NULL,
            UNIQUE(card_id, phone_id),
            FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE CASCADE,
            FOREIGN KEY(phone_id) REFERENCES phones(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()

@app.on_event("startup")
def on_startup():
    init_db()

# ---------- Pydantic Models ----------
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
    def non_empty_list(cls, v):
        if not isinstance(v, list) or len(v) == 0:
            raise ValueError("phone_numbers must be a non‑empty list")
        cleaned = [p.strip() for p in v if p and p.strip()]
        if len(cleaned) != len(v):
            raise ValueError("phone numbers must not be empty strings")
        return cleaned

class RetrieveCardsResponse(BaseModel):
    card_numbers: List[str] = Field(..., description="Credit cards associated with all given phone numbers.")

# ---------- Helper Functions ----------
def get_or_create_card(conn: sqlite3.Connection, number: str) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM cards WHERE number = ?", (number,))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute("INSERT INTO cards (number) VALUES (?)", (number,))
    conn.commit()
    return cur.lastrowid

def get_or_create_phone(conn: sqlite3.Connection, number: str) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM phones WHERE number = ?", (number,))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute("INSERT INTO phones (number) VALUES (?)", (number,))
    conn.commit()
    return cur.lastrowid

def associate_card_phone(conn: sqlite3.Connection, card_id: int, phone_id: int):
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT OR IGNORE INTO card_phone (card_id, phone_id) VALUES (?, ?)",
            (card_id, phone_id),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=400, detail="Invalid association data") from e

def find_cards_associated_with_all_phones(conn: sqlite3.Connection, phone_numbers: List[str]) -> List[str]:
    placeholders = ",".join("?" for _ in phone_numbers)
    query = f"""
        SELECT c.number AS card_number
        FROM cards c
        JOIN card_phone cp ON c.id = cp.card_id
        JOIN phones p ON cp.phone_id = p.id
        WHERE p.number IN ({placeholders})
        GROUP BY c.id
        HAVING COUNT(DISTINCT p.number) = ?
    """
    cur = conn.cursor()
    cur.execute(query, (*phone_numbers, len(phone_numbers)))
    rows = cur.fetchall()
    return [row["card_number"] for row in rows]

# ---------- Endpoints ----------
@app.post("/associate_card", status_code=status.HTTP_201_CREATED)
def associate_card(request: AssociateCardRequest):
    conn = get_db_connection()
    try:
        card_id = get_or_create_card(conn, request.credit_card)
        phone_id = get_or_create_phone(conn, request.phone)
        associate_card_phone(conn, card_id, phone_id)
    finally:
        conn.close()
    return JSONResponse(status_code=status.HTTP_201_CREATED, content={"detail": "Association created successfully"})

@app.post("/retrieve_cards", response_model=RetrieveCardsResponse)
def retrieve_cards(request: RetrieveCardsRequest):
    conn = get_db_connection()
    try:
        card_numbers = find_cards_associated_with_all_phones(conn, request.phone_numbers)
    finally:
        conn.close()
    if not card_numbers:
        raise HTTPException(status_code=404, detail="No credit cards found for the given phone numbers")
    return RetrieveCardsResponse(card_numbers=card_numbers)

# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, log_level="info")