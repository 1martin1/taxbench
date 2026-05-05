import os
import sqlite3
from typing import List, Tuple, Iterable

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator, conint, constr

# Constants for validation and limits
MAX_CREDIT_CARD_LENGTH = 19          # typical max length (including spaces/dashes)
MAX_PHONE_LENGTH = 15                # E.164 max length without '+'
MAX_PHONE_COUNT = 100                # reasonable upper bound for batch queries
MAX_REQUEST_BODY_SIZE = 1_048_576    # 1 MB
SQLITE_MAX_VARIABLE_NUMBER = 999    # SQLite limit for host parameters

DATABASE_PATH = "db.sqlite3"

app = FastAPI(title="Credit Card Phone Association Service", version="1.0.0")


def get_db():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with sqlite3.connect(DATABASE_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS credit_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number TEXT NOT NULL UNIQUE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS phones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number TEXT NOT NULL UNIQUE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS card_phone (
                card_id INTEGER NOT NULL,
                phone_id INTEGER NOT NULL,
                PRIMARY KEY (card_id, phone_id),
                FOREIGN KEY (card_id) REFERENCES credit_cards(id) ON DELETE CASCADE,
                FOREIGN KEY (phone_id) REFERENCES phones(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


@app.on_event("startup")
def on_startup():
    init_db()


# Middleware to enforce request size limit via Content-Length header
@app.middleware("http")
async def enforce_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_REQUEST_BODY_SIZE:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"detail": "Request body too large"},
                )
        except ValueError:
            pass  # ignore malformed header, let downstream handle
    response = await call_next(request)
    return response


class AssociateRequest(BaseModel):
    credit_card: constr(strip_whitespace=True, min_length=1, max_length=MAX_CREDIT_CARD_LENGTH) = Field(
        ..., description="Number of the credit card"
    )
    phone: constr(strip_whitespace=True, min_length=1, max_length=MAX_PHONE_LENGTH) = Field(
        ..., description="Phone number"
    )

    @validator("credit_card", "phone")
    def non_empty(cls, v: str):
        if not v:
            raise ValueError("must not be empty")
        return v


class RetrieveRequest(BaseModel):
    phone_numbers: List[constr(strip_whitespace=True, min_length=1, max_length=MAX_PHONE_LENGTH)] = Field(
        ..., description="Phone numbers"
    )

    @validator("phone_numbers")
    def validate_and_dedup(cls, v: List[str]):
        if not v:
            raise ValueError("phone_numbers must contain at least one phone number")
        if len(v) > MAX_PHONE_COUNT:
            raise ValueError(f"Maximum number of phone numbers is {MAX_PHONE_COUNT}")
        cleaned = [p.strip() for p in v if p and p.strip()]
        if len(cleaned) != len(v):
            raise ValueError("phone numbers must not be empty strings")
        # Deduplicate while preserving order
        seen = set()
        deduped = []
        for num in cleaned:
            if num not in seen:
                seen.add(num)
                deduped.append(num)
        return deduped


class RetrieveResponse(BaseModel):
    card_numbers: List[str] = Field(..., description="Credit cards associated with all given phone numbers.")


def chunked(iterable: Iterable, size: int) -> Iterable[Tuple]:
    """Yield successive chunks from iterable as tuples of length <= size."""
    it = iter(iterable)
    while True:
        chunk = tuple([x for _, x in zip(range(size), it)])
        if not chunk:
            break
        yield chunk


@app.post(
    "/associate_card",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new association of a credit card number with a phone number.",
)
def associate_card(request: AssociateRequest, db: sqlite3.Connection = Depends(get_db)):
    cur = db.cursor()
    # Insert or ignore credit card
    cur.execute(
        "INSERT OR IGNORE INTO credit_cards (number) VALUES (?)",
        (request.credit_card,),
    )
    # Insert or ignore phone
    cur.execute(
        "INSERT OR IGNORE INTO phones (number) VALUES (?)",
        (request.phone,),
    )
    # Retrieve ids
    cur.execute("SELECT id FROM credit_cards WHERE number = ?", (request.credit_card,))
    card_row = cur.fetchone()
    if not card_row:
        raise HTTPException(status_code=500, detail="Failed to retrieve credit card id")
    card_id = card_row["id"]

    cur.execute("SELECT id FROM phones WHERE number = ?", (request.phone,))
    phone_row = cur.fetchone()
    if not phone_row:
        raise HTTPException(status_code=500, detail="Failed to retrieve phone id")
    phone_id = phone_row["id"]

    # Insert association, ignore if already exists
    cur.execute(
        "INSERT OR IGNORE INTO card_phone (card_id, phone_id) VALUES (?, ?)",
        (card_id, phone_id),
    )
    db.commit()
    return {"detail": "Association created successfully"}


@app.post(
    "/retrieve_cards",
    response_model=RetrieveResponse,
    summary="Retrieve cards associated with a set of phone numbers. A card must be associated with all given phone numbers to be returned.",
)
def retrieve_cards(request: RetrieveRequest, db: sqlite3.Connection = Depends(get_db)):
    phone_numbers = request.phone_numbers

    # ----- Step 1: Resolve phone IDs (handle large IN lists via chunking) -----
    phone_ids = []
    placeholders_needed = len(phone_numbers)
    if placeholders_needed == 0:
        return RetrieveResponse(card_numbers=[])

    # Build query in chunks if needed
    for chunk in chunked(phone_numbers, SQLITE_MAX_VARIABLE_NUMBER):
        placeholders = ",".join("?" for _ in chunk)
        cur = db.cursor()
        cur.execute(
            f"SELECT id FROM phones WHERE number IN ({placeholders})",
            tuple(chunk),
        )
        rows = cur.fetchall()
        phone_ids.extend(row["id"] for row in rows)

    # If some numbers were not found, we treat as no matching cards (but do not expose via 404)
    if len(phone_ids) != len(phone_numbers):
        return RetrieveResponse(card_numbers=[])

    # ----- Step 2: Find card IDs linked to all provided phone IDs -----
    # Use GROUP BY/HAVING; also chunk phone_ids if necessary
    card_ids = set()
    for chunk in chunked(phone_ids, SQLITE_MAX_VARIABLE_NUMBER):
        placeholders = ",".join("?" for _ in chunk)
        query = f"""
            SELECT cp.card_id
            FROM card_phone cp
            WHERE cp.phone_id IN ({placeholders})
            GROUP BY cp.card_id
            HAVING COUNT(DISTINCT cp.phone_id) = ?
        """
        cur = db.cursor()
        cur.execute(query, (*chunk, len(chunk)))
        rows = cur.fetchall()
        card_ids.update(row["card_id"] for row in rows)

    if not card_ids:
        return RetrieveResponse(card_numbers=[])

    # ----- Step 3: Retrieve card numbers for the found card IDs -----
    card_numbers = []
    for chunk in chunked(list(card_ids), SQLITE_MAX_VARIABLE_NUMBER):
        placeholders = ",".join("?" for _ in chunk)
        cur = db.cursor()
        cur.execute(
            f"SELECT number FROM credit_cards WHERE id IN ({placeholders})",
            tuple(chunk),
        )
        rows = cur.fetchall()
        card_numbers.extend(row["number"] for row in rows)

    return RetrieveResponse(card_numbers=card_numbers)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)