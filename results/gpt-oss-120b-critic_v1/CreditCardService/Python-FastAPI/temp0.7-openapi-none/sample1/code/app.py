import os
import sqlite3
from typing import List, Set

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
DB_PATH = "db.sqlite3"
MAX_PHONE_NUMBER_LENGTH = 20          # reasonable limit for phone strings
MAX_CREDIT_CARD_LENGTH = 30           # typical max length for credit card numbers
MAX_PHONE_NUMBERS_IN_REQUEST = 20     # prevent excessively large queries
MAX_REQUEST_BODY_SIZE = 1024 * 1024   # 1 MB limit for JSON payloads

# --------------------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------------------
app = FastAPI(
    title="Credit Card Phone Association Service",
    version="1.0.0",
    # The OpenAPI spec already documents 400 responses; we will map validation
    # errors to 400 via a custom handler below.
)

# --------------------------------------------------------------------------
# Database utilities
# --------------------------------------------------------------------------
def get_connection() -> sqlite3.Connection:
    """Return a thread‑safe SQLite connection."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they do not exist."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT UNIQUE NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS phones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT UNIQUE NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS card_phone (
            card_id INTEGER NOT NULL,
            phone_id INTEGER NOT NULL,
            PRIMARY KEY (card_id, phone_id),
            FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE,
            FOREIGN KEY (phone_id) REFERENCES phones(id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()
    conn.close()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# --------------------------------------------------------------------------
# Pydantic models
# --------------------------------------------------------------------------
class AssociateRequest(BaseModel):
    credit_card: str = Field(
        ...,
        description="Number of the credit card",
        max_length=MAX_CREDIT_CARD_LENGTH,
    )
    phone: str = Field(
        ...,
        description="Phone number",
        max_length=MAX_PHONE_NUMBER_LENGTH,
    )

    @validator("credit_card", "phone")
    def not_empty_and_trim(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v


class RetrieveRequest(BaseModel):
    phone_numbers: List[str] = Field(
        ...,
        description="Phone numbers",
        # Pydantic does not support max_items directly; we enforce via validator.
    )

    @validator("phone_numbers")
    def validate_phone_numbers(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("phone_numbers must contain at least one item")
        if len(v) > MAX_PHONE_NUMBERS_IN_REQUEST:
            raise ValueError(
                f"phone_numbers list must not contain more than {MAX_PHONE_NUMBERS_IN_REQUEST} items"
            )
        for item in v:
            if not isinstance(item, str):
                raise ValueError("each phone number must be a string")
            trimmed = item.strip()
            if not trimmed:
                raise ValueError("phone numbers must be non‑empty strings")
            if len(trimmed) > MAX_PHONE_NUMBER_LENGTH:
                raise ValueError(
                    f"phone number exceeds maximum length of {MAX_PHONE_NUMBER_LENGTH}"
                )
        return v


class RetrieveResponse(BaseModel):
    card_numbers: List[str] = Field(
        ..., description="Credit cards associated with all given phone numbers."
    )


# --------------------------------------------------------------------------
# Database helper functions
# --------------------------------------------------------------------------
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


def associate_card_phone(credit_card: str, phone: str) -> None:
    conn = get_connection()
    try:
        card_id = get_or_create_card(conn, credit_card)
        phone_id = get_or_create_phone(conn, phone)
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO card_phone (card_id, phone_id) VALUES (?, ?)",
            (card_id, phone_id),
        )
        conn.commit()
    finally:
        conn.close()


def retrieve_cards_by_phones(phone_numbers: List[str]) -> List[str]:
    # Use distinct set for counting to handle duplicate phone numbers correctly.
    distinct_numbers: Set[str] = set(phone_numbers)
    placeholders = ",".join("?" for _ in distinct_numbers)
    query = f"""
        SELECT c.number
        FROM cards c
        JOIN card_phone cp ON c.id = cp.card_id
        JOIN phones p ON cp.phone_id = p.id
        WHERE p.number IN ({placeholders})
        GROUP BY c.id
        HAVING COUNT(DISTINCT p.number) = ?
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        # Parameter order: distinct phone numbers then the distinct count.
        cur.execute(query, (*distinct_numbers, len(distinct_numbers)))
        rows = cur.fetchall()
        return [row["number"] for row in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Middleware for request size limiting
# --------------------------------------------------------------------------
@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    # Only enforce on POST/PUT/PATCH as they have bodies.
    if request.method in ("POST", "PUT", "PATCH"):
        body = await request.body()
        if len(body) > MAX_REQUEST_BODY_SIZE:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Request payload too large"},
            )
        # Replace request stream with the already read body for downstream handlers.
        async def receive():
            return {"type": "http.request", "body": body}
        request._receive = receive  # type: ignore
    response = await call_next(request)
    return response


# --------------------------------------------------------------------------
# Exception handlers
# --------------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Convert FastAPI's 422 validation errors to 400 as per the OpenAPI spec.
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": exc.errors()},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.post(
    "/associate_card",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new association of a credit card number with a phone number.",
    operationId="associateCard",
)
def associate_card_endpoint(request: AssociateRequest):
    try:
        associate_card_phone(request.credit_card, request.phone)
    except sqlite3.Error:
        raise HTTPException(status_code=400, detail="Database error")
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={"detail": "Association created successfully"},
    )


@app.post(
    "/retrieve_cards",
    response_model=RetrieveResponse,
    summary="Retrieve cards associated with a set of phone numbers. A card must be associated with all given phone numbers to be returned.",
    operationId="retrieveCard",
)
def retrieve_cards_endpoint(request: RetrieveRequest):
    card_numbers = retrieve_cards_by_phones(request.phone_numbers)
    if not card_numbers:
        raise HTTPException(
            status_code=404, detail="No credit cards found for the provided phone numbers"
        )
    return RetrieveResponse(card_numbers=card_numbers)


# --------------------------------------------------------------------------
# Run server
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)