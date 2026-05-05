import os
import sqlite3
import time
import threading
from typing import List, Optional, Dict

from fastapi import FastAPI, HTTPException, Depends, status, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator, ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
DATABASE_PATH = "db.sqlite3"
MAX_REQUEST_SIZE = 1_048_576  # 1 MB
MAX_PHONE_NUMBERS = 500       # safety limit to stay under SQLite variable limit
RATE_LIMIT = 100              # requests
RATE_PERIOD = 60              # seconds

# ----------------------------------------------------------------------
# Database utilities
# ----------------------------------------------------------------------
def get_db():
    """Provide a SQLite connection per request."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS cards (
            credit_card TEXT PRIMARY KEY
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS phones (
            phone TEXT PRIMARY KEY
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS card_phone (
            credit_card TEXT,
            phone TEXT,
            PRIMARY KEY (credit_card, phone),
            FOREIGN KEY (credit_card) REFERENCES cards(credit_card) ON DELETE CASCADE,
            FOREIGN KEY (phone) REFERENCES phones(phone) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------
# Middleware
# ----------------------------------------------------------------------
class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests with a body larger than MAX_REQUEST_SIZE."""

    async def dispatch(self, request: Request, call_next):
        receive = request._receive  # type: ignore

        async def limited_receive() -> dict:
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                if len(body) > MAX_REQUEST_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Request body too large",
                    )
            return message

        request._receive = limited_receive  # type: ignore
        return await call_next(request)


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """Simple in‑memory rate limiter per client IP."""

    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.ip_requests: Dict[str, List[float]] = {}
        self.lock = threading.Lock()

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "anonymous"
        now = time.time()
        with self.lock:
            timestamps = self.ip_requests.get(client_ip, [])
            # Remove timestamps older than RATE_PERIOD
            timestamps = [ts for ts in timestamps if now - ts < RATE_PERIOD]
            timestamps.append(now)
            self.ip_requests[client_ip] = timestamps
            if len(timestamps) > RATE_LIMIT:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded",
                )
        return await call_next(request)


# ----------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------
app = FastAPI(title="Credit Card Phone Association Service", version="1.0.0")
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RateLimiterMiddleware)

init_db()


# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
def _strip(v: str) -> str:
    return v.strip()


class AssociateRequest(BaseModel):
    credit_card: str = Field(..., description="Number of the credit card")
    phone: str = Field(..., description="Phone number")

    @validator("credit_card")
    def validate_credit_card(cls, v: str):
        v = _strip(v)
        if not v.isdigit():
            raise ValueError("credit_card must contain only digits")
        if not 13 <= len(v) <= 19:
            raise ValueError("credit_card length must be between 13 and 19 digits")
        return v

    @validator("phone")
    def validate_phone(cls, v: str):
        v = _strip(v)
        if not v.replace("+", "", 1).isdigit():
            raise ValueError("phone must contain only digits and optional leading '+'")
        if not 7 <= len(v) <= 15:
            raise ValueError("phone length must be between 7 and 15 characters")
        return v


class RetrieveRequest(BaseModel):
    phone_numbers: List[str] = Field(..., description="Phone numbers")

    @validator("phone_numbers")
    def validate_phone_numbers(cls, v: List[str]):
        if not v:
            raise ValueError("phone_numbers must contain at least one phone number")
        cleaned: List[str] = []
        seen = set()
        for phone in v:
            phone = _strip(phone)
            if not phone:
                raise ValueError("phone numbers must be non‑empty strings")
            if not phone.replace("+", "", 1).isdigit():
                raise ValueError("phone must contain only digits and optional leading '+'")
            if not 7 <= len(phone) <= 15:
                raise ValueError("phone length must be between 7 and 15 characters")
            if phone not in seen:
                seen.add(phone)
                cleaned.append(phone)
        if len(cleaned) > MAX_PHONE_NUMBERS:
            raise ValueError(f"Maximum of {MAX_PHONE_NUMBERS} distinct phone numbers allowed")
        return cleaned


class RetrieveResponse(BaseModel):
    card_numbers: List[str] = Field(..., description="Credit cards associated with all given phone numbers.")


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post(
    "/associate_card",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new association of a credit card number with a phone number.",
)
def associate_card(
    request: AssociateRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    cursor = db.cursor()
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO cards (credit_card) VALUES (?)",
            (request.credit_card,),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO phones (phone) VALUES (?)",
            (request.phone,),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO card_phone (credit_card, phone) VALUES (?, ?)",
            (request.credit_card, request.phone),
        )
        db.commit()
    except sqlite3.Error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Database error")
    # Return an empty body as per OpenAPI spec for 201
    return Response(status_code=status.HTTP_201_CREATED)


@app.post(
    "/retrieve_cards",
    response_model=RetrieveResponse,
    summary="Retrieve cards associated with a set of phone numbers.",
)
def retrieve_cards(
    request: RetrieveRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    phone_list = request.phone_numbers
    placeholders = ",".join("?" for _ in phone_list)

    query = f"""
        SELECT credit_card
        FROM card_phone
        WHERE phone IN ({placeholders})
        GROUP BY credit_card
        HAVING COUNT(DISTINCT phone) = ?
    """
    try:
        cursor = db.cursor()
        cursor.execute(query, (*phone_list, len(phone_list)))
        rows = cursor.fetchall()
    except sqlite3.Error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Database error")

    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No credit cards found for the given phone numbers")

    card_numbers = [row["credit_card"] for row in rows]
    return RetrieveResponse(card_numbers=card_numbers)


# ----------------------------------------------------------------------
# Run server
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)