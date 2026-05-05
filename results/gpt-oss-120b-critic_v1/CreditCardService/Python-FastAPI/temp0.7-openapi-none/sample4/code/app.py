import os
import sqlite3
import time
from collections import defaultdict
from typing import List, Dict

from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    Request,
    status,
)
from pydantic import BaseModel, Field, conlist, constr

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_PATH = "db.sqlite3"
MAX_BODY_SIZE = 10 * 1024  # 10 KiB
MAX_PHONE_NUMBER_LENGTH = 20
MAX_CREDIT_CARD_LENGTH = 19
MAX_PHONE_NUMBERS_IN_QUERY = 10
RATE_LIMIT_REQUESTS = 60  # max requests
RATE_LIMIT_WINDOW = 60  # seconds

# ---------------------------------------------------------------------------
# Application Setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Credit Card Phone Association Service", version="1.0.0")


def init_db() -> None:
    """Create the SQLite database and required tables if they do not exist."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS association (
                credit_card TEXT NOT NULL,
                phone TEXT NOT NULL,
                UNIQUE(credit_card, phone)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


def get_db():
    """FastAPI dependency that provides a SQLite connection per request."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Rate limiting middleware (simple in‑memory implementation)
# ---------------------------------------------------------------------------

class RateLimiterMiddleware:
    """
    Very simple IP‑based rate limiter.
    Not suitable for production clusters but mitigates obvious DoS attempts.
    """

    def __init__(self, app: FastAPI):
        self.app = app
        self.requests_log: Dict[str, List[float]] = defaultdict(list)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client_ip = scope.get("client")[0] if scope.get("client") else "unknown"
        now = time.time()
        timestamps = self.requests_log[client_ip]

        # Remove timestamps older than the window
        while timestamps and timestamps[0] <= now - RATE_LIMIT_WINDOW:
            timestamps.pop(0)

        if len(timestamps) >= RATE_LIMIT_REQUESTS:
            response = {
                "detail": "Too many requests, please try again later."
            }
            await self.app.exception_handler(HTTPException)(
                Request(scope=scope, receive=receive), HTTPException(status_code=429, detail=response["detail"])
            )
            return

        timestamps.append(now)
        await self.app(scope, receive, send)


app.add_middleware(RateLimiterMiddleware)


# ---------------------------------------------------------------------------
# Request body size limiting middleware
# ---------------------------------------------------------------------------

class BodySizeLimitMiddleware:
    """Reject requests with a body larger than MAX_BODY_SIZE."""

    def __init__(self, app: FastAPI):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        body = await request.body()
        if len(body) > MAX_BODY_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Request body too large",
            )
        # Re‑inject the body for downstream handlers
        async def receive_with_body():
            return {"type": "http.request", "body": body, "more_body": False}
        await self.app(scope, receive_with_body, send)


app.add_middleware(BodySizeLimitMiddleware)


# ---------------------------------------------------------------------------
# Pydantic models with validation constraints
# ---------------------------------------------------------------------------

class AssociateRequest(BaseModel):
    credit_card: constr(min_length=1, max_length=MAX_CREDIT_CARD_LENGTH) = Field(
        ..., description="Number of the credit card"
    )
    phone: constr(min_length=1, max_length=MAX_PHONE_NUMBER_LENGTH) = Field(
        ..., description="Phone number"
    )


class RetrieveRequest(BaseModel):
    phone_numbers: conlist(
        item_type=constr(min_length=1, max_length=MAX_PHONE_NUMBER_LENGTH),
        min_items=1,
        max_items=MAX_PHONE_NUMBERS_IN_QUERY,
    ) = Field(..., description="Phone numbers")


class RetrieveResponse(BaseModel):
    card_numbers: List[str] = Field(..., description="Credit cards associated with all given phone numbers.")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post(
    "/associate_card",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Association created successfully"},
        400: {"description": "Invalid request"},
    },
)
def associate_card(
    payload: AssociateRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Store an association between a credit card number and a phone number.
    The operation is idempotent – duplicate entries are ignored.
    """
    try:
        db.execute(
            "INSERT INTO association (credit_card, phone) VALUES (?, ?)",
            (payload.credit_card, payload.phone),
        )
        db.commit()
    except sqlite3.IntegrityError:
        # Duplicate association – treat as success (idempotent)
        pass
    return {"detail": "Association created successfully"}


@app.post(
    "/retrieve_cards",
    response_model=RetrieveResponse,
    responses={
        200: {"description": "Credit cards found"},
        400: {"description": "Invalid request"},
        404: {"description": "Not found"},
    },
)
def retrieve_cards(
    payload: RetrieveRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Return credit card numbers that are associated with *all* of the supplied phone numbers.
    """
    phone_numbers = payload.phone_numbers

    placeholders = ",".join("?" for _ in phone_numbers)
    query = f"""
        SELECT credit_card
        FROM association
        WHERE phone IN ({placeholders})
        GROUP BY credit_card
        HAVING COUNT(DISTINCT phone) = ?
    """
    params = (*phone_numbers, len(phone_numbers))
    cursor = db.execute(query, params)
    rows = [row[0] for row in cursor.fetchall()]

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No credit cards found for the given phone numbers",
        )
    return RetrieveResponse(card_numbers=rows)


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)