import os
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from threading import Lock
from typing import Any, Deque, Generator

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


DB_PATH = "db.sqlite3"

# Resource and abuse-control limits
MAX_ITEM_ID = 2_147_483_647
MIN_ITEM_ID = -2_147_483_648
MAX_COUNT = 2_147_483_647
MIN_COUNT = -2_147_483_648
MAX_ITEMS_PER_CART_RESPONSE = 1000
MAX_CARTS_TOTAL = 10000
CART_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 120


app = FastAPI(
    title="Shopping Cart Service",
    description="A simple shopping cart service API.",
    version="1.0.0",
)


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._requests[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                return False
            bucket.append(now)
            return True


rate_limiter = RateLimiter(
    max_requests=RATE_LIMIT_MAX_REQUESTS,
    window_seconds=RATE_LIMIT_WINDOW_SECONDS,
)


def get_client_identifier(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
        if client_ip:
            return client_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS carts (
                cart_id TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cart_items (
                cart_id TEXT NOT NULL,
                item_id INTEGER NOT NULL,
                count INTEGER NOT NULL,
                PRIMARY KEY (cart_id, item_id),
                FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE,
                CHECK (count > 0)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cart_items_cart_id ON cart_items(cart_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_carts_created_at ON carts(created_at)"
        )
        conn.commit()
    finally:
        conn.close()


def cleanup_expired_carts(conn: sqlite3.Connection) -> None:
    cutoff = int(time.time()) - CART_TTL_SECONDS
    conn.execute("DELETE FROM carts WHERE created_at < ?", (cutoff,))


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class CreateCartResponse(BaseModel):
    cart_id: str = Field(..., description="The unique identifier for the created cart")


class AddToCartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cart_id: str = Field(..., description="The unique identifier of the cart")
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The number of items to add (or remove if negative)")


class RetrieveCartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cart_id: str = Field(..., description="The unique identifier of the cart")


class CartItemResponse(BaseModel):
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The count of the item in the cart")


class RetrieveCartResponse(BaseModel):
    items: list[CartItemResponse]


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next: Any) -> Response:
    client_id = get_client_identifier(request)
    if not rate_limiter.check(client_id):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Too many requests"},
        )
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    if request.url.path == "/add_to_cart":
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Invalid request"},
        )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.post(
    "/create_cart",
    response_model=CreateCartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new shopping cart",
    operation_id="createCart",
)
def create_cart() -> CreateCartResponse:
    cart_id = str(uuid.uuid4())
    now = int(time.time())

    try:
        with get_db() as conn:
            cleanup_expired_carts(conn)
            total_carts = conn.execute("SELECT COUNT(*) AS count FROM carts").fetchone()
            if total_carts is not None and int(total_carts["count"]) >= MAX_CARTS_TOTAL:
                oldest = conn.execute(
                    "SELECT cart_id FROM carts ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
                if oldest is not None:
                    conn.execute("DELETE FROM carts WHERE cart_id = ?", (oldest["cart_id"],))

            conn.execute(
                "INSERT INTO carts (cart_id, created_at) VALUES (?, ?)",
                (cart_id, now),
            )
    except sqlite3.Error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )

    return CreateCartResponse(cart_id=cart_id)


@app.post(
    "/add_to_cart",
    status_code=status.HTTP_200_OK,
    summary="Add or remove items from the cart",
    operation_id="addToCart",
    responses={
        400: {"description": "Invalid request"},
        404: {"description": "Cart not found"},
    },
)
def add_to_cart(payload: AddToCartRequest) -> Response:
    if not (MIN_ITEM_ID <= payload.item_id <= MAX_ITEM_ID):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )
    if not (MIN_COUNT <= payload.count <= MAX_COUNT):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request",
        )

    try:
        with get_db() as conn:
            cleanup_expired_carts(conn)

            cart = conn.execute(
                "SELECT cart_id FROM carts WHERE cart_id = ?",
                (payload.cart_id,),
            ).fetchone()

            if cart is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Cart not found",
                )

            existing = conn.execute(
                "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
                (payload.cart_id, payload.item_id),
            ).fetchone()

            current_count = int(existing["count"]) if existing is not None else 0
            new_count = current_count + payload.count

            if new_count < 0 or new_count > MAX_COUNT:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid request",
                )

            if new_count == 0:
                if existing is not None:
                    conn.execute(
                        "DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
                        (payload.cart_id, payload.item_id),
                    )
            elif existing is None:
                conn.execute(
                    "INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)",
                    (payload.cart_id, payload.item_id, new_count),
                )
            else:
                conn.execute(
                    "UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?",
                    (new_count, payload.cart_id, payload.item_id),
                )
    except sqlite3.Error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )

    return Response(status_code=status.HTTP_200_OK)


@app.post(
    "/retrieve_cart",
    response_model=RetrieveCartResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all items in the cart",
    operation_id="retrieveCart",
    responses={
        404: {"description": "Cart not found"},
    },
)
def retrieve_cart(payload: RetrieveCartRequest) -> RetrieveCartResponse:
    try:
        with get_db() as conn:
            cleanup_expired_carts(conn)

            cart = conn.execute(
                "SELECT cart_id FROM carts WHERE cart_id = ?",
                (payload.cart_id,),
            ).fetchone()

            if cart is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Cart not found",
                )

            rows = conn.execute(
                """
                SELECT item_id, count
                FROM cart_items
                WHERE cart_id = ?
                ORDER BY item_id ASC
                LIMIT ?
                """,
                (payload.cart_id, MAX_ITEMS_PER_CART_RESPONSE + 1),
            ).fetchall()
    except sqlite3.Error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )

    if len(rows) > MAX_ITEMS_PER_CART_RESPONSE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )

    items = [CartItemResponse(item_id=row["item_id"], count=row["count"]) for row in rows]
    return RetrieveCartResponse(items=items)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)