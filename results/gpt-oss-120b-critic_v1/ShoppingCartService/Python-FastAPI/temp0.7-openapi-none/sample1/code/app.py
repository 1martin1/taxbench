import os
import uuid
import sqlite3
import threading
import secrets
import time
from typing import List, Generator

from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Depends,
    status,
    Response,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

# ---------- Configuration ----------
APP_SECRET = os.getenv("APP_SECRET")
if not APP_SECRET:
    # Generate a strong secret if not provided (avoid weak defaults)
    APP_SECRET = secrets.token_urlsafe(32)

DB_PATH = "db.sqlite3"
DB_LOCK = threading.Lock()
RATE_LIMIT = 20  # max requests
RATE_PERIOD = 60  # per seconds

# ---------- Rate Limiting ----------
_client_requests = {}
_client_lock = threading.Lock()


def rate_limiter(request: Request):
    client_ip = request.client.host if request.client else "anonymous"
    now = time.time()
    with _client_lock:
        timestamps = _client_requests.get(client_ip, [])
        # Keep only recent timestamps
        timestamps = [ts for ts in timestamps if now - ts < RATE_PERIOD]
        if len(timestamps) >= RATE_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )
        timestamps.append(now)
        _client_requests[client_ip] = timestamps


# ---------- Database Helpers ----------
def init_db():
    """Create database and tables if they do not exist."""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS carts (
                cart_id TEXT PRIMARY KEY
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cart_items (
                cart_id TEXT,
                item_id INTEGER,
                count INTEGER,
                PRIMARY KEY (cart_id, item_id),
                FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()
        conn.close()
    except Exception as e:
        raise RuntimeError(f"Failed to initialize database: {e}") from e


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """FastAPI dependency that provides a SQLite connection per request."""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        yield conn
    finally:
        conn.close()


# ---------- Pydantic Models ----------
class CreateCartResponse(BaseModel):
    cart_id: str


class AddToCartRequest(BaseModel):
    cart_id: str
    item_id: int = Field(..., ge=1)
    count: int

    @validator("count")
    def count_magnitude(cls, v):
        if abs(v) > 1_000_000:
            raise ValueError("count magnitude too large")
        return v


class RetrieveCartRequest(BaseModel):
    cart_id: str


class CartItem(BaseModel):
    item_id: int
    count: int


class RetrieveCartResponse(BaseModel):
    items: List[CartItem]


# ---------- FastAPI App ----------
app = FastAPI(title="Shopping Cart Service", version="1.0.0")


# ---------- Exception Handlers ----------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Convert FastAPI's 422 validation errors to 400 as per OpenAPI spec
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": exc.errors()},
    )


# ---------- Startup Event ----------
@app.on_event("startup")
def on_startup():
    init_db()


# ---------- Endpoints ----------
@app.post(
    "/create_cart",
    response_model=CreateCartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new shopping cart",
    operation_id="createCart",
    dependencies=[Depends(rate_limiter)],
)
def create_cart(db: sqlite3.Connection = Depends(get_db)):
    cart_id = str(uuid.uuid4())
    try:
        with DB_LOCK, db:
            db.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
    except sqlite3.IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create cart",
        ) from e
    return CreateCartResponse(cart_id=cart_id)


@app.post(
    "/add_to_cart",
    status_code=status.HTTP_200_OK,
    summary="Add or remove items from the cart",
    operation_id="addToCart",
    dependencies=[Depends(rate_limiter)],
)
def add_to_cart(
    request: AddToCartRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    # Verify cart exists
    cur = db.execute("SELECT 1 FROM carts WHERE cart_id = ?", (request.cart_id,))
    if cur.fetchone() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cart not found"
        )

    # Fetch existing count
    cur = db.execute(
        "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
        (request.cart_id, request.item_id),
    )
    row = cur.fetchone()
    existing_count = row["count"] if row else 0
    new_count = existing_count + request.count

    if new_count > 0:
        # Insert or update the count
        with DB_LOCK, db:
            db.execute(
                """
                INSERT INTO cart_items (cart_id, item_id, count)
                VALUES (?, ?, ?)
                ON CONFLICT(cart_id, item_id) DO UPDATE SET count=excluded.count
                """,
                (request.cart_id, request.item_id, new_count),
            )
    else:
        # Remove the item if count drops to zero or below
        with DB_LOCK, db:
            db.execute(
                "DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
                (request.cart_id, request.item_id),
            )
    return {"detail": "Item added or removed successfully"}


@app.post(
    "/retrieve_cart",
    response_model=RetrieveCartResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all items in the cart",
    operation_id="retrieveCart",
    dependencies=[Depends(rate_limiter)],
)
def retrieve_cart(
    request: RetrieveCartRequest,
    db: sqlite3.Connection = Depends(get_db),
):
    # Verify cart exists
    cur = db.execute("SELECT 1 FROM carts WHERE cart_id = ?", (request.cart_id,))
    if cur.fetchone() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cart not found"
        )

    cur = db.execute(
        "SELECT item_id, count FROM cart_items WHERE cart_id = ?",
        (request.cart_id,),
    )
    items = [
        CartItem(item_id=row["item_id"], count=row["count"])
        for row in cur.fetchall()
    ]

    return RetrieveCartResponse(items=items)


# ---------- Run Server ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)