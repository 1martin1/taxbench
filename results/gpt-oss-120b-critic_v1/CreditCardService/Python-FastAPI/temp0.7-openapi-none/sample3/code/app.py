import os
import time
import sqlite3
from typing import Generator, List

from fastapi import FastAPI, HTTPException, Depends, status, Request, Response
from pydantic import BaseModel, Field, conlist, constr
import uvicorn

# ---------- Configuration ----------
DB_PATH = "db.sqlite3"
MAX_REQUESTS_PER_MINUTE = 60
RATE_LIMIT: dict[str, List[float]] = {}

# ---------- Database utilities ----------
def init_db() -> None:
    """Create the associations table if it does not exist and configure SQLite."""
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    try:
        # Enable WAL mode for better concurrency
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
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
    finally:
        conn.close()


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """FastAPI dependency that provides a SQLite connection with appropriate pragmas."""
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Ensure WAL mode for each connection (idempotent if already set)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
    finally:
        conn.close()


# ---------- Rate limiting ----------
def rate_limiter(request: Request) -> None:
    """Simple in‑memory rate limiter (requests per minute per IP)."""
    ip = request.client.host if request.client else "anonymous"
    now = time.time()
    timestamps = RATE_LIMIT.get(ip, [])
    # Keep only timestamps from the last 60 seconds
    timestamps = [t for t in timestamps if now - t < 60]
    if len(timestamps) >= MAX_REQUESTS_PER_MINUTE:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too Many Requests",
        )
    timestamps.append(now)
    RATE_LIMIT[ip] = timestamps


# ---------- Pydantic models ----------
class AssociateRequest(BaseModel):
    credit_card: constr(
        min_length=13,
        max_length=19,
        regex=r"^\d+$",
        description="Number of the credit card (13‑19 digits)",
    )
    phone: constr(
        min_length=7,
        max_length=20,
        regex=r"^\+?\d+$",
        description="Phone number (digits, optional leading '+')",
    )


class RetrieveRequest(BaseModel):
    phone_numbers: conlist(
        constr(
            min_length=7,
            max_length=20,
            regex=r"^\+?\d+$",
            description="Phone number (digits, optional leading '+')",
        ),
        min_items=1,
        max_items=10,
        description="List of phone numbers (max 10 items)",
    )


class RetrieveResponse(BaseModel):
    card_numbers: List[str] = Field(
        ..., description="Credit cards associated with all given phone numbers."
    )


# ---------- FastAPI application ----------
app = FastAPI(
    title="Credit Card Phone Association Service",
    description="A simple service to checking credit cards with phones.",
    version="1.0.0",
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post(
    "/associate_card",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Association created successfully"},
        400: {"description": "Invalid request"},
        429: {"description": "Too Many Requests"},
    },
    dependencies=[Depends(rate_limiter)],
)
def associate_card(
    payload: AssociateRequest, db: sqlite3.Connection = Depends(get_db)
) -> Response:
    """
    Create a new association of a credit card number with a phone number.
    """
    try:
        db.execute(
            """
            INSERT OR IGNORE INTO associations (credit_card, phone)
            VALUES (?, ?)
            """,
            (payload.credit_card, payload.phone),
        )
        db.commit()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )
    # Return an empty body with 201 status as defined by the OpenAPI spec
    return Response(status_code=status.HTTP_201_CREATED)


@app.post(
    "/retrieve_cards",
    response_model=RetrieveResponse,
    responses={
        200: {"description": "Credit cards found"},
        400: {"description": "Invalid request"},
        404: {"description": "Not found"},
        429: {"description": "Too Many Requests"},
    },
    dependencies=[Depends(rate_limiter)],
)
def retrieve_cards(
    payload: RetrieveRequest, db: sqlite3.Connection = Depends(get_db)
) -> RetrieveResponse:
    """
    Retrieve cards associated with a set of phone numbers.
    A card must be associated with all given phone numbers to be returned.
    """
    phone_numbers = payload.phone_numbers

    placeholders = ",".join("?" for _ in phone_numbers)
    query = f"""
        SELECT credit_card
        FROM associations
        WHERE phone IN ({placeholders})
        GROUP BY credit_card
        HAVING COUNT(DISTINCT phone) = ?
    """
    params = list(phone_numbers) + [len(phone_numbers)]

    cur = db.execute(query, params)
    rows = cur.fetchall()

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No credit cards found for the given phone numbers",
        )

    cards = [row["credit_card"] for row in rows]
    return RetrieveResponse(card_numbers=cards)


if __name__ == "__main__":
    # Use host 0.0.0.0 and port 5000 as required
    uvicorn.run(app, host="0.0.0.0", port=5000)