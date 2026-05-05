import os
import sqlite3
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from typing import Deque, Dict, Generator, List, Optional, Tuple

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator


DB_PATH = "db.sqlite3"

MAX_REQUEST_BODY_BYTES = 4096
MAX_CART_ID_LENGTH = 128
MAX_ABS_COUNT = 1_000_000
MAX_ITEM_ID = 2_147_483_647
MAX_ITEMS_PER_CART = 1000
MAX_CARTS_TOTAL = 10000

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 120
RATE_LIMIT_MAX_CREATE_CARTS = 30


_rate_lock = threading.Lock()
_request_buckets: Dict[str, Deque[float]] = defaultdict(deque)
_create_cart_buckets: Dict[str, Deque[float]] = defaultdict(deque)


def _prune_bucket(bucket: Deque[float], now: float, window_seconds: int) -> None:
    cutoff = now - window_seconds
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()


def _check_rate_limit(client_key: str, bucket_map: Dict[str, Deque[float]], limit: int) -> bool:
    now = time.time()
    with _rate_lock:
        bucket = bucket_map[client_key]
        _prune_bucket(bucket, now, RATE_LIMIT_WINDOW_SECONDS)
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS carts (
                cart_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cart_items (
                cart_id TEXT NOT NULL,
                item_id INTEGER NOT NULL,
                count INTEGER NOT NULL CHECK (count > 0),
                PRIMARY KEY (cart_id, item_id),
                FOREIGN KEY (cart_id) REFERENCES carts(cart_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cart_items_cart_id_item_id ON cart_items(cart_id, item_id)"
        )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        yield conn
    finally:
        conn.close()


def get_client_key(request: Request) -> str:
    client = request.client
    if client and client.host:
        return client.host
    return "unknown"


def enforce_request_size(request: Request) -> None:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_REQUEST_BODY_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Request body too large",
                )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Content-Length header",
            )


def get_cart_row(conn: sqlite3.Connection, cart_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT cart_id FROM carts WHERE cart_id = ?",
        (cart_id,),
    ).fetchone()


def get_cart_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS count FROM carts").fetchone()
    return int(row["count"])


def get_item_count_for_cart(conn: sqlite3.Connection, cart_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM cart_items WHERE cart_id = ?",
        (cart_id,),
    ).fetchone()
    return int(row["count"])


class CreateCartResponse(BaseModel):
    cart_id: str = Field(..., description="The unique identifier for the created cart")


class AddToCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The number of items to add (or remove if negative)")

    @field_validator("cart_id")
    @classmethod
    def validate_cart_id(cls, value: str) -> str:
        if not value or len(value) > MAX_CART_ID_LENGTH:
            raise ValueError("Invalid cart_id")
        return value

    @field_validator("item_id")
    @classmethod
    def validate_item_id(cls, value: int) -> int:
        if value < 0 or value > MAX_ITEM_ID:
            raise ValueError("Invalid item_id")
        return value

    @field_validator("count")
    @classmethod
    def validate_count(cls, value: int) -> int:
        if value == 0:
            raise ValueError("count must not be zero")
        if abs(value) > MAX_ABS_COUNT:
            raise ValueError("count magnitude too large")
        return value


class RetrieveCartRequest(BaseModel):
    cart_id: str = Field(..., description="The unique identifier of the cart")

    @field_validator("cart_id")
    @classmethod
    def validate_cart_id(cls, value: str) -> str:
        if not value or len(value) > MAX_CART_ID_LENGTH:
            raise ValueError("Invalid cart_id")
        return value


class CartItem(BaseModel):
    item_id: int = Field(..., description="The unique identifier of the item")
    count: int = Field(..., description="The count of the item in the cart")

    model_config = ConfigDict(from_attributes=True)


class RetrieveCartResponse(BaseModel):
    items: List[CartItem]


app = FastAPI(
    title="Shopping Cart Service",
    description="A simple shopping cart service API.",
    version="1.0.0",
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    enforce_request_size(request)

    client_key = get_client_key(request)
    if not _check_rate_limit(client_key, _request_buckets, RATE_LIMIT_MAX_REQUESTS):
        return Response(status_code=status.HTTP_429_TOO_MANY_REQUESTS)

    response = await call_next(request)
    return response


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.post(
    "/create_cart",
    response_model=CreateCartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new shopping cart",
    operation_id="createCart",
)
def create_cart(request: Request) -> CreateCartResponse:
    client_key = get_client_key(request)
    if not _check_rate_limit(client_key, _create_cart_buckets, RATE_LIMIT_MAX_CREATE_CARTS):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
        )

    cart_id = str(uuid.uuid4())

    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        total_carts = get_cart_count(conn)
        if total_carts >= MAX_CARTS_TOTAL:
            conn.execute("ROLLBACK")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Cart capacity reached",
            )
        conn.execute("INSERT INTO carts (cart_id) VALUES (?)", (cart_id,))
        conn.execute("COMMIT")

    return CreateCartResponse(cart_id=cart_id)


@app.post(
    "/add_to_cart",
    status_code=status.HTTP_200_OK,
    summary="Add or remove items from the cart",
    operation_id="addToCart",
    response_class=Response,
    responses={
        200: {"description": "Item added or removed successfully"},
        400: {"description": "Invalid request"},
        404: {"description": "Cart not found"},
    },
)
def add_to_cart(payload: AddToCartRequest) -> Response:
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")

        cart = get_cart_row(conn, payload.cart_id)
        if cart is None:
            conn.execute("ROLLBACK")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cart not found",
            )

        existing_item = conn.execute(
            "SELECT count FROM cart_items WHERE cart_id = ? AND item_id = ?",
            (payload.cart_id, payload.item_id),
        ).fetchone()

        current_count = int(existing_item["count"]) if existing_item is not None else 0
        new_count = current_count + payload.count

        if new_count < 0:
            conn.execute("ROLLBACK")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Resulting item count cannot be negative",
            )

        if existing_item is None and payload.count > 0:
            distinct_items = get_item_count_for_cart(conn, payload.cart_id)
            if distinct_items >= MAX_ITEMS_PER_CART:
                conn.execute("ROLLBACK")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cart item limit reached",
                )

        if new_count == 0:
            conn.execute(
                "DELETE FROM cart_items WHERE cart_id = ? AND item_id = ?",
                (payload.cart_id, payload.item_id),
            )
        elif existing_item is None:
            conn.execute(
                "INSERT INTO cart_items (cart_id, item_id, count) VALUES (?, ?, ?)",
                (payload.cart_id, payload.item_id, new_count),
            )
        else:
            conn.execute(
                "UPDATE cart_items SET count = ? WHERE cart_id = ? AND item_id = ?",
                (new_count, payload.cart_id, payload.item_id),
            )

        conn.execute("COMMIT")

    return Response(status_code=status.HTTP_200_OK)


@app.post(
    "/retrieve_cart",
    response_model=RetrieveCartResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all items in the cart",
    operation_id="retrieveCart",
    responses={
        200: {
            "description": "List of items in the cart",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "item_id": {
                                            "type": "integer",
                                            "description": "The unique identifier of the item",
                                        },
                                        "count": {
                                            "type": "integer",
                                            "description": "The count of the item in the cart",
                                        },
                                    },
                                },
                            }
                        },
                    }
                }
            },
        },
        404: {"description": "Cart not found"},
    },
)
def retrieve_cart(payload: RetrieveCartRequest) -> RetrieveCartResponse:
    with get_db() as conn:
        cart = get_cart_row(conn, payload.cart_id)

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
            ORDER BY item_id
            LIMIT ?
            """,
            (payload.cart_id, MAX_ITEMS_PER_CART),
        ).fetchall()

    items = [CartItem(item_id=int(row["item_id"]), count=int(row["count"])) for row in rows]
    return RetrieveCartResponse(items=items)


if __name__ == "__main__":
    init_db()
    host = "0.0.0.0" if os.getenv("ALLOW_PUBLIC_BIND") == "1" else "127.0.0.1"
    uvicorn.run(app, host=host, port=5000)